"""SQLite = single source of truth. Three tables, foreign keys, idempotent writes.

LEARN: SQL basics, schema design, foreign keys, the sqlite3 module,
       idempotency (why upsert on email_id stops duplicate rows on re-runs).
"""
from __future__ import annotations
import sqlite3
from paths import DB_PATH
from schemas import Job, FitScore

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    email_id TEXT UNIQUE,          -- idempotency key (Gmail message id)
    source TEXT, company TEXT, title TEXT, jd_text TEXT,
    location TEXT, salary TEXT, deadline TEXT, url TEXT,
    fit_score INTEGER, fit_rationale TEXT, track TEXT,
    ingested_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    status TEXT DEFAULT 'interested',   -- interested|applied|interviewing|rejected|offer
    applied_date TEXT, resume_id INTEGER, notes TEXT
);
CREATE TABLE IF NOT EXISTS resumes (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    version INTEGER, file_path TEXT, approved INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def init() -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.executescript(SCHEMA)


def upsert_job(email_id: str, job: Job) -> int:
    """Insert a job, or update it if this email_id was already ingested.

    Idempotent: re-running the pipeline over the same inbox never creates
    duplicate rows. The ON CONFLICT clause fires because email_id is UNIQUE.
    Returns the row's id (needed by set_fit and the resume stage).
    """
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            """
            INSERT INTO jobs (email_id, source, company, title, jd_text,
                              location, salary, deadline, url)
            VALUES (:email_id, :source, :company, :title, :jd_text,
                    :location, :salary, :deadline, :url)
            ON CONFLICT(email_id) DO UPDATE SET
                source=excluded.source, company=excluded.company,
                title=excluded.title, jd_text=excluded.jd_text,
                location=excluded.location, salary=excluded.salary,
                deadline=excluded.deadline, url=excluded.url
            """,
            {
                "email_id": email_id,
                "source": job.source,
                "company": job.company,
                "title": job.title,
                "jd_text": job.jd_text,
                "location": job.location,
                "salary": job.salary,
                # SQLite has no date type; store ISO text (or NULL).
                "deadline": job.deadline.isoformat() if job.deadline else None,
                "url": job.url,
            },
        )
        # lastrowid is unreliable on the UPDATE path, so look the id up by key.
        row = c.execute("SELECT id FROM jobs WHERE email_id = ?", (email_id,)).fetchone()
        return row[0]


def set_fit(job_id: int, fit: FitScore) -> None:
    """Write the fit-score results onto an existing job row."""
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            "UPDATE jobs SET fit_score = ?, fit_rationale = ?, track = ? WHERE id = ?",
            (fit.score, fit.rationale, fit.track, job_id),
        )


def all_jobs() -> list[dict]:
    """Every job as a dict, best fit first (unscored rows sink to the bottom)."""
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row  # rows behave like dicts instead of tuples
        rows = c.execute(
            "SELECT * FROM jobs ORDER BY fit_score IS NULL, fit_score DESC"
        ).fetchall()
        return [dict(r) for r in rows]
