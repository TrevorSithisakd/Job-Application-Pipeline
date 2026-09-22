"""LOCAL WEB API over the pipeline. Replaces the Streamlit dashboard.

FastAPI does two jobs: expose the pipeline as a JSON API under /api/*, and serve
the static frontend (frontend/) at the root. The custom HTML/CSS/JS front end
talks to these endpoints with fetch(); nothing here knows about presentation.

Run it via the launcher (run_app.py / run_app.bat), or directly:
    python -m uvicorn api:app --reload
"""
from __future__ import annotations
import copy
import json
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import db
import seed
import settings
from paths import ROOT

# Copy the example profile / fact bank into place on a fresh install, as a
# template for Setup to show. A no-op once you have your own.
#
# This used to be load-bearing for the imports below, which read profile.md /
# fact_bank.md at import time and crashed without them. They are read lazily now,
# so the app boots either way.
seed.ensure_files()

import llm
import pipeline
from schemas import (AppSettings, EmailSource, Job, JobPreferences,
                     ScoringRubric)
from stages import factbank, fitscore, ingest
from stages.tailor import tailor_job

db.init()          # schema + grounding migration

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


# --- Background jobs: started here, polled by the UI --------------------------
# Three long operations now run this way (ingest, rescore, inbox scan), so the
# shared-state-dict pattern the ingest used is factored out rather than copied
# twice more. One instance per job kind; each refuses to start while its own run
# is in flight. The worker threads open their own sqlite connections per call,
# so this stays thread-safe.

class BackgroundJob:
    """A single-slot background task with a pollable progress dict.

    The dict IS the API response — workers update it in place (the same contract
    pipeline.run(progress=...) already expects), and the /status endpoint returns
    it verbatim.
    """

    def __init__(self, **fields):
        self._defaults = fields
        self.state: dict = {"running": False, "done": True, "error": None,
                            "message": "", **fields}

    @property
    def running(self) -> bool:
        return bool(self.state["running"])

    def start(self, target, *args) -> None:
        # copy.deepcopy, not **self._defaults: a mutable default (the scan's
        # senders list) would otherwise be the SAME object on every run, so one
        # run appending to it would leak into the next.
        self.state.update(running=True, done=False, error=None,
                          message="starting…", **copy.deepcopy(self._defaults))
        threading.Thread(target=self._run, args=(target, *args), daemon=True).start()

    def _run(self, target, *args) -> None:
        try:
            target(*args)
        except Exception as e:
            # Surface the class name: "RuntimeError: No DEEPSEEK_API_KEY set" is
            # actionable in the UI, a bare traceback in the server log is not.
            self.state["error"] = f"{type(e).__name__}: {e}"
        finally:
            self.state.update(running=False, done=True)


_ingest = BackgroundJob(scored=0, skipped=0)
_rescore = BackgroundJob(scored=0, skipped=0, total=0)
_scan = BackgroundJob(scanned=0, senders=[])


class IngestReq(BaseModel):
    days: int = 7


@app.post("/api/ingest")
def start_ingest(payload: IngestReq) -> dict:
    """Kick off an ingest over the last `days` days. Pre-flights Gmail auth,
    because a browser fetch can't complete the OAuth consent flow."""
    if _ingest.running:
        raise HTTPException(409, "an ingest is already running")
    if not ingest.credentials_ready():
        raise HTTPException(409, "Gmail authorization needed — run `python -m pipeline` "
                                 "once in your terminal to sign in, then try again.")
    days = max(1, min(payload.days, 30))
    _ingest.start(lambda: pipeline.run(days=days, progress=_ingest.state))
    return {"started": True, "days": days}


@app.get("/api/ingest/status")
def ingest_status() -> dict:
    return _ingest.state


# --- Rescore: re-apply changed criteria to jobs already on the board ----------

class RescoreReq(BaseModel):
    # False re-scores everything, including jobs whose first scoring failed.
    only_scored: bool = True


def _run_rescore(only_scored: bool) -> None:
    rows = db.all_jobs()
    targets = [r for r in rows if r.get("fit_score") is not None] if only_scored else rows
    _rescore.state.update(total=len(targets))
    scored = skipped = 0
    for row in targets:
        try:
            job = db.get_job(row["id"])
            db.set_fit(row["id"], fitscore.fit_score(job))
            scored += 1
        except Exception as e:
            # One unscoreable job (a thin JD, a transient API error) must not
            # abandon the rest of the batch — same isolation as pipeline.run().
            skipped += 1
            print(f"  [skip rescore] {row['id']}: {type(e).__name__}: {e}")
        _rescore.state.update(scored=scored, skipped=skipped,
                              message=f"{row['company']} - {row['title']}")
    _rescore.state.update(message="done")


@app.post("/api/jobs/rescore")
def start_rescore(payload: RescoreReq) -> dict:
    """Re-score jobs against the CURRENT preferences and rubric. Changing the
    criteria is only useful if you can re-apply them to what's already here."""
    if _rescore.running:
        raise HTTPException(409, "a rescore is already running")
    if not llm.has_key():
        raise HTTPException(409, llm.NO_KEY_MESSAGE)
    _rescore.start(_run_rescore, payload.only_scored)
    return {"started": True}


@app.get("/api/jobs/rescore/status")
def rescore_status() -> dict:
    return _rescore.state


@app.get("/api/jobs/rescore/estimate")
def rescore_estimate(only_scored: bool = True) -> dict:
    """How many jobs a rescore would touch. Shown before the button is pressed —
    each one is a paid LLM call, so the cost shouldn't be a surprise."""
    rows = db.all_jobs()
    n = len([r for r in rows if r.get("fit_score") is not None]) if only_scored else len(rows)
    return {"jobs": n, "calls": n, "tier": "cheap"}


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


# --- Setup: everything the app needs to be yours ------------------------------
# The whole point of this block is that none of it should require editing a file
# by hand or restarting the server. That works only because llm/fitscore/tailor
# resolve their config per call now instead of at import.

@app.get("/api/settings")
def get_settings() -> dict:
    """Everything the Setup pane renders in one call, plus the flags that drive
    the first-run banner. The API key itself is never returned — only whether one
    is configured and a masked preview."""
    s = settings.load()
    profile = settings.get_profile()
    fact_bank = settings.get_fact_bank()
    return {
        "key": settings.api_key_status(),
        "preferences": s.preferences.model_dump(),
        "rubric": s.rubric.model_dump(),
        "email_sources": [e.model_dump() for e in s.email_sources],
        "sources_configured": s.sources_configured,
        # What ingest would query right now — the built-in fallback list when
        # nothing is configured, so the pane can show what it is defaulting to.
        "effective_sources": settings.sender_values(),
        "gmail_ready": ingest.credentials_ready(),
        "profile": {k: v for k, v in profile.items() if k != "content"},
        "fact_bank": {k: v for k, v in fact_bank.items() if k != "content"},
        # What the banner needs: anything true here means setup is incomplete.
        "needs": {
            "key": not settings.api_key_status()["configured"],
            "profile": (not profile["exists"]) or profile["is_example"],
            "fact_bank": (not fact_bank["exists"]) or fact_bank["is_example"],
            "sources": not s.sources_configured,
        },
    }


@app.get("/api/setup/status")
def setup_status() -> dict:
    """What the frontend asks on boot: wizard or board?"""
    return settings.setup_status()


@app.post("/api/setup/complete")
def complete_setup() -> dict:
    """The wizard's final button. Re-checks on the server rather than trusting the
    page: the gate is only worth anything if skipping the UI can't bypass it."""
    missing = [k for k, ok in settings.required_items().items() if not ok]
    if missing:
        raise HTTPException(409, {"message": "Setup is not finished yet.", "missing": missing})
    settings.mark_setup_complete()
    return settings.setup_status()


class KeyReq(BaseModel):
    key: str


@app.post("/api/settings/key")
def set_key(payload: KeyReq) -> dict:
    """Write the key to .env and drop llm.py's cached client."""
    try:
        settings.set_api_key(payload.key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **settings.api_key_status()}


@app.post("/api/settings/key/test")
def test_key() -> dict:
    """One cheap round-trip against the configured key. Finding out here beats
    finding out three stages into a tailor run."""
    try:
        reply = llm.smoke_test()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "reply": (reply or "").strip()[:80]}


@app.put("/api/settings/preferences")
def set_preferences(payload: JobPreferences) -> dict:
    """What an ideal role looks like. FastAPI validates against the pydantic
    model, so a bad value is a 422 with the offending field named."""
    s = settings.load()
    s.preferences = payload
    settings.save(s)
    return s.preferences.model_dump()


@app.put("/api/settings/rubric")
def set_rubric(payload: ScoringRubric) -> dict:
    """How fit is judged. Saving does NOT rescore — that's an explicit, paid
    action behind its own button."""
    s = settings.load()
    s.rubric = payload
    settings.save(s)
    return s.rubric.model_dump()


@app.post("/api/settings/rubric/preview")
def preview_rubric(payload: ScoringRubric) -> dict:
    """The exact prompt the current sliders produce, without saving.

    Sliders that silently rewrite a hidden prompt are a black box; this makes the
    translation inspectable, which matters because the prompt is the actual
    scoring logic.
    """
    return {"prompt": fitscore.build_rubric(settings.load().preferences, payload)}


class DocReq(BaseModel):
    content: str


@app.get("/api/settings/profile")
def get_profile_doc() -> dict:
    return settings.get_profile()


@app.put("/api/settings/profile")
def set_profile_doc(payload: DocReq) -> dict:
    return {"ok": True, "chars": settings.set_profile(payload.content)}


@app.get("/api/settings/factbank")
def get_factbank_doc() -> dict:
    return settings.get_fact_bank()


@app.put("/api/settings/factbank")
def set_factbank_doc(payload: DocReq) -> dict:
    return {"ok": True, "chars": settings.set_fact_bank(payload.content)}


@app.post("/api/settings/factbank/extract")
async def extract_factbank(file: UploadFile = File(...)) -> dict:
    """Upload a resume, get fact-bank markdown BACK — this deliberately does not
    save it.

    The grounding check treats the fact bank as ground truth, so an LLM writing
    that file unreviewed would let an extraction error certify itself as a
    verified resume claim. The pane shows this in the editor and you press Save.
    """
    ext = Path(file.filename or "resume.docx").suffix.lower()
    if ext not in (".docx", ".pdf"):
        raise HTTPException(400, "upload a .docx or .pdf file")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if not llm.has_key():
        raise HTTPException(409, llm.NO_KEY_MESSAGE)
    try:
        markdown = factbank.extract_from_upload(data, ext)
    except ValueError as e:
        raise HTTPException(400, str(e))          # unreadable/scanned file
    return {"content": markdown, "saved": False}


# --- Email sources ------------------------------------------------------------

class SourcesReq(BaseModel):
    sources: list[EmailSource]


@app.get("/api/settings/senders")
def get_senders() -> dict:
    s = settings.load()
    return {
        "sources": [e.model_dump() for e in s.email_sources],
        "configured": s.sources_configured,
        # What ingest would actually query right now, defaults included.
        "effective": settings.sender_values(),
        "defaults": [e.model_dump() for e in settings.default_sources()],
    }


@app.put("/api/settings/senders")
def put_senders(payload: SourcesReq) -> dict:
    s = settings.set_sources(payload.sources)
    return {"ok": True, "sources": [e.model_dump() for e in s.email_sources],
            "effective": settings.sender_values()}


class ScanReq(BaseModel):
    days: int = 90


def _run_scan(days: int) -> None:
    found = ingest.discover_senders(query=f"category:updates newer_than:{days}d",
                                    progress=_scan.state)
    _scan.state.update(senders=found, message=f"found {len(found)} senders")


@app.post("/api/settings/senders/scan")
def start_scan(payload: ScanReq) -> dict:
    """Sweep the inbox for anything that looks like a job-alert sender.

    Not run automatically after auth: it makes one metadata call per message over
    a wide window, so it is slow enough that doing it unasked would make a first
    launch look hung. It stays a button press.
    """
    if _scan.running:
        raise HTTPException(409, "a scan is already running")
    if not ingest.credentials_ready():
        raise HTTPException(409, "Gmail authorization needed — run `python -m pipeline` "
                                 "once in your terminal to sign in, then try again.")
    days = max(1, min(payload.days, 365))
    _scan.start(_run_scan, days)
    return {"started": True, "days": days}


@app.get("/api/settings/senders/scan/status")
def scan_status() -> dict:
    return _scan.state


# Serve the frontend LAST so it doesn't shadow the /api routes above. html=True
# makes "/" serve index.html.
from fastapi.staticfiles import StaticFiles   # noqa: E402  (after routes on purpose)
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
