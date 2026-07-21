"""ORCHESTRATOR — wires the four stages end to end. This is the `run one command`.

LEARN: pipeline composition, where errors are caught, run idempotency, logging.
"""
from __future__ import annotations
import db
from stages import ingest, extract, fitscore, tailor


def run() -> None:
    db.init()
    emails = ingest.fetch_job_emails()
    print(f"Fetched {len(emails)} emails.")

    scored = skipped = 0
    for email_id, body in emails:
        # Isolate each email. Job alerts sit alongside application-status mail
        # that isn't a posting at all, so extraction failing is expected traffic,
        # not a crash — one bad email must not discard the whole run.
        try:
            job = extract.extract(body)             # stage 2 (validated)
            job_id = db.upsert_job(email_id, job)   # store (idempotent)
            fit = fitscore.fit_score(job)           # stage 3
            db.set_fit(job_id, fit)                 # store
            scored += 1
            print(f"  [{fit.score:>3}] {job.company} - {job.title}")
        except Exception as e:
            skipped += 1
            print(f"  [skip] {email_id}: {type(e).__name__}: {e}")
        # Resume stage is opt-in per job (it costs the quality model):
        # draft = tailor.draft_resume(job); save as pending for approval.

    print(f"\nRun complete. {scored} scored, {skipped} skipped.")
    print("Open the dashboard: streamlit run dashboard.py")


if __name__ == "__main__":
    run()
