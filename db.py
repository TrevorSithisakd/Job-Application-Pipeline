"""SQLite = single source of truth. Three tables, foreign keys, idempotent writes.

LEARN: SQL basics, schema design, foreign keys, the sqlite3 module,
       idempotency (why upsert on email_id stops duplicate rows on re-runs).
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from paths import DB_PATH, RESUMES_DIR
from schemas import Job, FitScore, ResumeDraft, GroundingResult

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
    grounded INTEGER,              -- 1 = every claim supported, 0 = something flagged
    grounding_json TEXT,           -- full GroundingResult for the dashboard
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def init() -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.executescript(SCHEMA)
        _migrate(c)


def _migrate(c: sqlite3.Connection) -> None:
    """Add columns to a resumes table created before grounding existed. SQLite
    has no 'ADD COLUMN IF NOT EXISTS', so check PRAGMA first — this keeps init()
    idempotent for DBs that predate Phase B."""
    cols = {row[1] for row in c.execute("PRAGMA table_info(resumes)").fetchall()}
    if "grounded" not in cols:
        c.execute("ALTER TABLE resumes ADD COLUMN grounded INTEGER")
    if "grounding_json" not in cols:
        c.execute("ALTER TABLE resumes ADD COLUMN grounding_json TEXT")


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


def get_job(job_id: int) -> Job:
    """Rehydrate a stored job into a Job model for the tailor stage.

    The tailor stage needs the JD (and company/title) as a validated object,
    not a raw row. Only the columns that map onto Job are selected, so the
    fit-score fields don't leak in. pydantic parses the ISO deadline text back
    into a date.
    """
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT source, company, title, jd_text, location, salary, deadline, url "
            "FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"No job with id {job_id}")
    return Job.model_validate(dict(row))


def save_resume(job_id: int, draft: ResumeDraft,
                grounding: GroundingResult | None = None) -> tuple[int, int, Path]:
    """Persist a tailored draft as a new pending version. Returns
    (resume_id, version, json_path).

    The structured JSON is the source of truth — the renderer (Phase C) and
    grounding check (Phase B) reload it, so we store that, not a rendered file.
    version auto-increments per job, so re-tailoring never clobbers a prior
    draft. Rows land approved=0; the dashboard flips that after human review.

    When a grounding result is given, its full report is stored (DB column + a
    sibling v<n>.grounding.json), and `grounded` records whether every claim was
    supported — the flag the approve gate reads.
    """
    out_dir = RESUMES_DIR / str(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as c:
        # COALESCE handles the first draft for a job (MAX over zero rows is NULL).
        version = c.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM resumes WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
        json_path = out_dir / f"v{version}.json"
        # Write the file before the row: a failed write leaves no dangling row.
        json_path.write_text(draft.model_dump_json(indent=2), encoding="utf-8")

        grounded = grounding_json = None
        if grounding is not None:
            grounding_json = grounding.model_dump_json(indent=2)
            grounded = 1 if grounding.all_supported else 0
            (out_dir / f"v{version}.grounding.json").write_text(
                grounding_json, encoding="utf-8")

        cur = c.execute(
            "INSERT INTO resumes (job_id, version, file_path, approved, "
            "grounded, grounding_json) VALUES (?, ?, ?, 0, ?, ?)",
            (job_id, version, str(json_path), grounded, grounding_json),
        )
        return cur.lastrowid, version, json_path
