"""ORCHESTRATOR — wires the four stages end to end. This is the `run one command`.

LEARN: pipeline composition, where errors are caught, run idempotency, logging.
"""
from __future__ import annotations
import db
from stages import ingest, extract, fitscore, tailor


def run() -> None:
    db.init()
    for email_id, body in ingest.fetch_job_emails():
        job = extract.extract(body)             # stage 2 (validated)
        job_id = db.upsert_job(email_id, job)   # store (idempotent)
        fit = fitscore.fit_score(job)           # stage 3
        db.set_fit(job_id, fit)                 # store
        # Resume stage is opt-in per job (it costs the quality model):
        # draft = tailor.draft_resume(job); save as pending for approval.
    print("Run complete. Open the dashboard: streamlit run dashboard.py")


if __name__ == "__main__":
    run()
