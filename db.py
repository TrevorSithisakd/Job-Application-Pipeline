"""SQLite = single source of truth. Three tables, foreign keys, idempotent writes.

LEARN: SQL basics, schema design, foreign keys, the sqlite3 module,
       idempotency (why upsert on email_id stops duplicate rows on re-runs).
"""
from __future__ import annotations
import re
import sqlite3
from pathlib import Path
from paths import DB_PATH, RESUMES_DIR
from schemas import Job, FitScore, ResumeDraft, GroundingResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    email_id TEXT,                 -- provenance: the alert email it came from
    dedup_key TEXT UNIQUE,         -- idempotency: normalized "company|title"
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
    """Bring an older DB up to the current schema. SQLite has no 'ADD COLUMN IF
    NOT EXISTS', so we check PRAGMA first — this keeps init() idempotent."""
    # resumes: grounding columns (Phase B).
    cols = {row[1] for row in c.execute("PRAGMA table_info(resumes)").fetchall()}
    if "grounded" not in cols:
        c.execute("ALTER TABLE resumes ADD COLUMN grounded INTEGER")
    if "grounding_json" not in cols:
        c.execute("ALTER TABLE resumes ADD COLUMN grounding_json TEXT")

    # jobs: move idempotency from a UNIQUE email_id to a per-job dedup_key, so a
    # single digest email can hold many jobs. Changing a column constraint means
    # rebuilding the table in SQLite. Ids are preserved so resumes.job_id stays
    # valid; INSERT OR IGNORE drops any (rare) duplicate url from the old data.
    jcols = {row[1] for row in c.execute("PRAGMA table_info(jobs)").fetchall()}
    if "dedup_key" not in jcols:
        c.executescript("""
            ALTER TABLE jobs RENAME TO jobs_old;
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                email_id TEXT,
                dedup_key TEXT UNIQUE,
                source TEXT, company TEXT, title TEXT, jd_text TEXT,
                location TEXT, salary TEXT, deadline TEXT, url TEXT,
                fit_score INTEGER, fit_rationale TEXT, track TEXT,
                ingested_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT OR IGNORE INTO jobs
                (id, email_id, dedup_key, source, company, title, jd_text,
                 location, salary, deadline, url, fit_score, fit_rationale,
                 track, ingested_at)
            SELECT id, email_id,
                   COALESCE(NULLIF(url, ''), email_id || ':' || id),
                   source, company, title, jd_text, location, salary, deadline,
                   url, fit_score, fit_rationale, track, ingested_at
            FROM jobs_old;
            DROP TABLE jobs_old;
        """)


def _dedup_key(job: Job) -> str:
    """Identity of a posting for dedup. NOT the url: the same job appears on
    LinkedIn/Indeed/SEEK with different urls, and urls carry per-email tracking
    tokens — so url-based dedup leaves the same role duplicated many times.
    Normalized company+title collapses those to one row. Normalization lowercases
    and squeezes all whitespace runs to a single space so "Data  Scientist" and
    "Data Scientist" match."""
    norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()
    return f"{norm(job.company)}|{norm(job.title)}"


def upsert_job(email_id: str, job: Job) -> int:
    """Insert a job, or update it if this posting was already ingested.

    Idempotent per POSTING, not per email: a digest email holds many jobs, and the
    same job recurs across boards/emails. The dedup key is normalized company+title
    (see _dedup_key), so re-running never creates duplicate rows. Returns the row's
    id (needed by set_fit and the resume stage).
    """
    dedup_key = _dedup_key(job)
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            """
            INSERT INTO jobs (email_id, dedup_key, source, company, title, jd_text,
                              location, salary, deadline, url)
            VALUES (:email_id, :dedup_key, :source, :company, :title, :jd_text,
                    :location, :salary, :deadline, :url)
            ON CONFLICT(dedup_key) DO UPDATE SET
                email_id=excluded.email_id, source=excluded.source,
                company=excluded.company, title=excluded.title,
                jd_text=excluded.jd_text, location=excluded.location,
                salary=excluded.salary, deadline=excluded.deadline,
                url=excluded.url
            """,
            {
                "email_id": email_id,
                "dedup_key": dedup_key,
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
        row = c.execute("SELECT id FROM jobs WHERE dedup_key = ?", (dedup_key,)).fetchone()
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


# --- Read/write helpers for the web API -------------------------------------

def get_job_row(job_id: int) -> dict | None:
    """The full job row as a dict (all columns, incl. fit score + JD), or None."""
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def resumes_for_job(job_id: int) -> list[dict]:
    """Every resume version for a job, newest first (no heavy grounding_json)."""
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT id, job_id, version, file_path, approved, grounded, created_at "
            "FROM resumes WHERE job_id = ? ORDER BY version DESC",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_resume(resume_id: int) -> dict | None:
    """One resume row (all columns, incl. grounding_json), or None."""
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()
        return dict(row) if row else None


def set_approved(resume_id: int, approved: bool = True) -> None:
    """Flip a resume's approval flag — the human gate, driven from the UI."""
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE resumes SET approved = ? WHERE id = ?",
                  (1 if approved else 0, resume_id))


def set_jd_text(job_id: int, jd_text: str) -> None:
    """Replace a job's description. Alert emails only carry a teaser, so the user
    pastes the full JD from the posting before tailoring; this stores it."""
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE jobs SET jd_text = ? WHERE id = ?", (jd_text, job_id))
