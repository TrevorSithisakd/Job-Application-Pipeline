"""LOCAL WEB API over the pipeline. Replaces the Streamlit dashboard.

FastAPI does two jobs: expose the pipeline as a JSON API under /api/*, and serve
the static frontend (frontend/) at the root. The custom HTML/CSS/JS front end
talks to these endpoints with fetch(); nothing here knows about presentation.

Run it via the launcher (run_app.py / run_app.bat), or directly:
    python -m uvicorn api:app --reload
"""
from __future__ import annotations
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import db
from paths import ROOT
from stages.tailor import tailor_job

db.init()   # schema + grounding migration, once at startup

app = FastAPI(title="Job Application Pipeline")

DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    """All jobs, best fit first — the main table."""
    return db.all_jobs()


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: int) -> dict:
    """One job with its full JD, fit rationale, and its resume versions."""
    row = db.get_job_row(job_id)
    if row is None:
        raise HTTPException(404, "job not found")
    row["resumes"] = db.resumes_for_job(job_id)
    return row


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
    }


@app.get("/api/resumes/{resume_id}")
def resume_detail(resume_id: int) -> dict:
    """One resume version, with its grounding report parsed for the UI."""
    row = db.get_resume(resume_id)
    if row is None:
        raise HTTPException(404, "resume not found")
    row["grounding"] = json.loads(row["grounding_json"]) if row.get("grounding_json") else None
    row.pop("grounding_json", None)
    return row


@app.post("/api/resumes/{resume_id}/approve")
def approve(resume_id: int) -> dict:
    """The human gate: mark a version approved (approved=1)."""
    if db.get_resume(resume_id) is None:
        raise HTTPException(404, "resume not found")
    db.set_approved(resume_id, True)
    return {"ok": True}


@app.get("/api/resumes/{resume_id}/docx")
def download_docx(resume_id: int) -> FileResponse:
    """Download the rendered .docx (sibling of the stored JSON)."""
    row = db.get_resume(resume_id)
    if row is None:
        raise HTTPException(404, "resume not found")
    docx = Path(row["file_path"]).with_suffix(".docx")
    if not docx.exists():
        raise HTTPException(404, "docx not rendered for this version")
    return FileResponse(docx, filename=docx.name, media_type=DOCX_MEDIA)


# Serve the frontend LAST so it doesn't shadow the /api routes above. html=True
# makes "/" serve index.html.
from fastapi.staticfiles import StaticFiles   # noqa: E402  (after routes on purpose)
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
