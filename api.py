"""LOCAL WEB API over the pipeline. Replaces the Streamlit dashboard.

FastAPI does two jobs: expose the pipeline as a JSON API under /api/*, and serve
the static frontend (frontend/) at the root. The custom HTML/CSS/JS front end
talks to these endpoints with fetch(); nothing here knows about presentation.

Run it via the launcher (run_app.py / run_app.bat), or directly:
    python -m uvicorn api:app --reload
"""
from __future__ import annotations
import json
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import db
import seed
from paths import ROOT

# Make required data files exist BEFORE importing the stages below, which read
# profile.md / fact_bank.md at import time. A no-op on a real local setup; on a
# bare/cloud deploy it materialises the demo persona (or your PROFILE_MD /
# FACT_BANK_MD env vars) so the app can start.
seed.ensure_files()

import pipeline
from schemas import Job
from stages import fitscore, ingest
from stages.tailor import tailor_job

db.init()          # schema + grounding migration
seed.ensure_seed() # populate a few demo jobs if the DB is empty (no-op if not)

app = FastAPI(title="Job Application Pipeline")

DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    """All jobs, best fit first — the board / table."""
    return db.all_jobs()


@app.get("/api/stats")
def stats() -> dict:
    """Aggregate counts for the board's health strip."""
    return db.stats()


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: int) -> dict:
    """One job with its full JD, fit rationale, missing keywords, and resumes."""
    row = db.get_job_row(job_id)
    if row is None:
        raise HTTPException(404, "job not found")
    row["missing_keywords"] = json.loads(row["missing_keywords"]) if row.get("missing_keywords") else []
    row["resumes"] = db.resumes_for_job(job_id)
    return row


class NewJob(BaseModel):
    company: str
    title: str
    jd_text: str = ""
    url: str | None = None
    location: str | None = None
    salary: str | None = None
    deadline: str | None = None


@app.post("/api/jobs")
def create_job(payload: NewJob) -> dict:
    """Manual entry for a role found outside the pipeline: insert with
    source=manual, then fit-score it like everything else."""
    dl = None
    if payload.deadline:
        try:
            dl = date.fromisoformat(payload.deadline)
        except ValueError:
            dl = None
    job = Job(source="manual", company=payload.company, title=payload.title,
              jd_text=payload.jd_text, url=payload.url, location=payload.location,
              salary=payload.salary, deadline=dl)
    job_id = db.upsert_job("manual", job)
    try:
        db.set_fit(job_id, fitscore.fit_score(job))
    except Exception:
        pass   # a thin JD can fail scoring; the row still lands on the board
    return db.get_job_row(job_id)


class StatusUpdate(BaseModel):
    status: str


@app.put("/api/jobs/{job_id}/status")
def update_status(job_id: int, payload: StatusUpdate) -> dict:
    """Move a job along the application board (drag-drop / segmented control)."""
    if db.get_job_row(job_id) is None:
        raise HTTPException(404, "job not found")
    db.set_status(job_id, payload.status)
    return {"ok": True, "status": payload.status}


@app.delete("/api/jobs/{job_id}")
def remove_job(job_id: int) -> dict:
    """Delete a job and its drafted resumes (rows + files)."""
    if db.get_job_row(job_id) is None:
        raise HTTPException(404, "job not found")
    db.delete_job(job_id)
    return {"ok": True}


# --- Ingest: run the pipeline in the background, polled by the UI -------------
# A single shared state dict; only one ingest runs at a time. The background
# thread opens its own sqlite connections per call, so this is thread-safe.
_ingest = {"running": False, "scored": 0, "skipped": 0, "message": "",
           "done": True, "error": None}


class IngestReq(BaseModel):
    days: int = 7


def _run_ingest(days: int) -> None:
    try:
        pipeline.run(days=days, progress=_ingest)
    except Exception as e:
        _ingest["error"] = f"{type(e).__name__}: {e}"
    finally:
        _ingest["running"] = False
        _ingest["done"] = True


@app.post("/api/ingest")
def start_ingest(payload: IngestReq) -> dict:
    """Kick off an ingest over the last `days` days. Pre-flights Gmail auth,
    because a browser fetch can't complete the OAuth consent flow."""
    if _ingest["running"]:
        raise HTTPException(409, "an ingest is already running")
    if not ingest.credentials_ready():
        raise HTTPException(409, "Gmail authorization needed — run `python -m pipeline` "
                                 "once in your terminal to sign in, then try again.")
    days = max(1, min(payload.days, 30))
    _ingest.update(running=True, scored=0, skipped=0, message="starting…",
                   done=False, error=None)
    threading.Thread(target=_run_ingest, args=(days,), daemon=True).start()
    return {"started": True, "days": days}


@app.get("/api/ingest/status")
def ingest_status() -> dict:
    return _ingest


class JDUpdate(BaseModel):
    jd_text: str


@app.put("/api/jobs/{job_id}/jd")
def update_jd(job_id: int, payload: JDUpdate) -> dict:
    """Save the full job description the user pasted from the posting. Tailoring
    reads jobs.jd_text, so the next tailor run uses this automatically."""
    if db.get_job_row(job_id) is None:
        raise HTTPException(404, "job not found")
    db.set_jd_text(job_id, payload.jd_text)
    return {"ok": True, "chars": len(payload.jd_text)}


@app.post("/api/jobs/{job_id}/tailor")
def tailor(job_id: int) -> dict:
    """Draft + grounding-check + render a new resume version for this job.
    Slow: it makes two quality-tier LLM calls, so the UI should show progress."""
    if db.get_job_row(job_id) is None:
        raise HTTPException(404, "job not found")
    result = tailor_job(job_id)
    return {
        "resume_id": result.resume_id,
        "version": result.version,
        "grounded": result.grounding.all_supported,
        "flagged": [c.claim for c in result.grounding.flagged],
        "pages": result.pages,
        "fill_pct": result.fill_pct,
        "fit_notes": result.fit_notes,
    }


@app.get("/api/resumes/{resume_id}")
def resume_detail(resume_id: int) -> dict:
    """One resume version, with its grounding report parsed for the UI."""
    row = db.get_resume(resume_id)
    if row is None:
        raise HTTPException(404, "resume not found")
    row["grounding"] = json.loads(row["grounding_json"]) if row.get("grounding_json") else None
    row.pop("grounding_json", None)
    # The structured draft (for the resume preview) lives beside the row in v<n>.json.
    try:
        row["draft"] = json.loads(Path(row["file_path"]).read_text(encoding="utf-8"))
    except Exception:
        row["draft"] = None
    return row


@app.post("/api/resumes/{resume_id}/approve")
def approve(resume_id: int) -> dict:
    """The human gate: mark a version approved (approved=1)."""
    if db.get_resume(resume_id) is None:
        raise HTTPException(404, "resume not found")
    db.set_approved(resume_id, True)
    return {"ok": True}


_MEDIA = {".docx": DOCX_MEDIA, ".pdf": "application/pdf"}


@app.get("/api/resumes/{resume_id}/docx")
def download_resume(resume_id: int) -> FileResponse:
    """Download a resume. Tailored versions store a .json whose sibling .docx we
    serve; uploaded versions store the file itself (.docx or .pdf)."""
    row = db.get_resume(resume_id)
    if row is None:
        raise HTTPException(404, "resume not found")
    fp = Path(row["file_path"])
    serve = fp.with_suffix(".docx") if fp.suffix == ".json" else fp
    if not serve.exists():
        raise HTTPException(404, "resume file not found")
    return FileResponse(serve, filename=serve.name,
                        media_type=_MEDIA.get(serve.suffix.lower(), "application/octet-stream"))


@app.post("/api/jobs/{job_id}/resume/upload")
async def upload_resume(job_id: int, file: UploadFile = File(...)) -> dict:
    """Attach an existing (non-tailored) resume file to a job as a new version."""
    if db.get_job_row(job_id) is None:
        raise HTTPException(404, "job not found")
    ext = Path(file.filename or "resume.docx").suffix.lower()
    if ext not in (".docx", ".pdf"):
        raise HTTPException(400, "upload a .docx or .pdf file")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    resume_id, version = db.save_uploaded_resume(job_id, data, ext)
    return {"resume_id": resume_id, "version": version, "source": "uploaded"}


# Serve the frontend LAST so it doesn't shadow the /api routes above. html=True
# makes "/" serve index.html.
from fastapi.staticfiles import StaticFiles   # noqa: E402  (after routes on purpose)
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
