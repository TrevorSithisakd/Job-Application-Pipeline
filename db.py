"""SQLite = single source of truth. Three tables, foreign keys, idempotent writes.

LEARN: SQL basics, schema design, foreign keys, the sqlite3 module,
       idempotency (why upsert on email_id stops duplicate rows on re-runs).
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from schemas import Job, FitScore

DB_PATH = Path("applications.db")

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
    """Idempotent: same email never creates two rows. TODO: return job id."""
    raise NotImplementedError


def set_fit(job_id: int, fit: FitScore) -> None:
    raise NotImplementedError


def all_jobs() -> list[dict]:
    raise NotImplementedError
