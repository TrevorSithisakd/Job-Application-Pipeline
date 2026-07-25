"""ORCHESTRATOR — wires the four stages end to end. This is the `run one command`.

LEARN: pipeline composition, where errors are caught, run idempotency, logging.
"""
from __future__ import annotations
import db
from stages import ingest, extract, fitscore, tailor


def run(days: int = 7, progress: dict | None = None) -> dict:
    """Ingest the last `days` days of alerts and score any NEW jobs.

    Already-scored jobs are skipped (re-runs stay fast and cheap — a re-ingest
    mostly re-sees the same postings). `progress`, if given, is a shared dict the
    web app polls; we push {scored, skipped, message} onto it. Returns the counts.
    """
    db.init()
    if progress is not None:
        progress.update(message="fetching emails…")
    emails = ingest.fetch_job_emails(days=days)
    print(f"Fetched {len(emails)} emails.")

    scored = skipped = 0
    for email_id, body in emails:
        # One email is often a DIGEST of many jobs, so extraction returns a list.
        # Isolate each email: application-status mail and newsletters extract to
        # [] (or raise), which is expected traffic, not a crash — one bad email
        # must not discard the whole run.
        try:
            jobs = extract.extract(body)            # stage 2 -> list[Job]
        except Exception as e:
            skipped += 1
            print(f"  [skip email] {email_id}: {type(e).__name__}: {e}")
            continue
        if not jobs:
            print(f"  [no jobs] {email_id}")        # not a posting email
            continue

        # Then score each job independently: one bad posting must not sink the
        # other eleven in the same digest.
        for job in jobs:
            try:
                job_id = db.upsert_job(email_id, job)   # store (idempotent per posting)
                if db.get_job_row(job_id).get("fit_score") is not None:
                    continue                            # already scored — skip (cheap re-runs)
                fit = fitscore.fit_score(job)           # stage 3
                db.set_fit(job_id, fit)                 # store
                scored += 1
                print(f"  [{fit.score:>3}] {job.company} - {job.title}")
                if progress is not None:
                    progress.update(scored=scored, skipped=skipped,
                                    message=f"{job.company} - {job.title}")
            except Exception as e:
                skipped += 1
                print(f"  [skip job] {job.company} - {job.title}: {type(e).__name__}: {e}")
        # Resume stage is opt-in per job (it costs the quality model):
        # tailor.tailor_job(job_id) drafts, grounds, and renders a pending resume.

    if progress is not None:
        progress.update(scored=scored, skipped=skipped, message="done")
    print(f"\nRun complete. {scored} scored, {skipped} skipped.")
    print("Open the app: run_app.bat   (or: python run_app.py)")
    return {"scored": scored, "skipped": skipped}


if __name__ == "__main__":
    import sys
    _days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    run(days=_days)
