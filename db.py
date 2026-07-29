"""SQLite = single source of truth. Three tables, foreign keys, idempotent writes.

LEARN: SQL basics, schema design, foreign keys, the sqlite3 module,
       idempotency (why upsert on email_id stops duplicate rows on re-runs).
"""
from __future__ import annotations
import json
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
    fit_score INTEGER, fit_rationale TEXT, track TEXT, missing_keywords TEXT,
    status TEXT DEFAULT 'interested',   -- application pipeline column (the board)
    ingested_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS resumes (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    version INTEGER, file_path TEXT, approved INTEGER DEFAULT 0,
    grounded INTEGER,              -- 1 = every claim supported, 0 = something flagged
    grounding_json TEXT,           -- full GroundingResult for the dashboard
    source TEXT DEFAULT 'tailored',-- 'tailored' (pipeline) or 'uploaded' (external file)
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
    if "source" not in cols:
        c.execute("ALTER TABLE resumes ADD COLUMN source TEXT DEFAULT 'tailored'")

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

    # jobs: additive columns for the application board + fit-score keywords.
    jcols2 = {row[1] for row in c.execute("PRAGMA table_info(jobs)").fetchall()}
    if "missing_keywords" not in jcols2:
        c.execute("ALTER TABLE jobs ADD COLUMN missing_keywords TEXT")
    if "status" not in jcols2:
        c.execute("ALTER TABLE jobs ADD COLUMN status TEXT DEFAULT 'interested'")


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
            "UPDATE jobs SET fit_score = ?, fit_rationale = ?, track = ?, "
            "missing_keywords = ? WHERE id = ?",
            (fit.score, fit.rationale, fit.track,
             json.dumps(fit.missing_keywords), job_id),
        )


def set_status(job_id: int, status: str) -> None:
    """Move a job along the application board (interested/applied/…)."""
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))


def stats() -> dict:
    """Aggregate counts for the board's health strip."""
    with sqlite3.connect(DB_PATH) as c:
        j = c.execute(
            "SELECT COUNT(*), COUNT(fit_score), MAX(fit_score) FROM jobs"
        ).fetchone()
        r = c.execute(
            "SELECT COUNT(*), COUNT(DISTINCT job_id), COALESCE(SUM(approved),0) FROM resumes"
        ).fetchone()
        return {"jobs": j[0], "scored": j[1] or 0, "top_fit": j[2] or 0,
                "drafts": r[0] or 0, "roles_with_drafts": r[1] or 0, "approved": r[2]}


def all_jobs() -> list[dict]:
    """Every job as a dict, best fit first (unscored rows sink to the bottom)."""
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row  # rows behave like dicts instead of tuples
        rows = c.execute(
            "SELECT j.*, "
            "(SELECT COUNT(*) FROM resumes r WHERE r.job_id = j.id) AS resume_count, "
            "(SELECT COALESCE(MAX(approved), 0) FROM resumes r WHERE r.job_id = j.id) "
            "  AS any_approved "
            "FROM jobs j ORDER BY fit_score IS NULL, fit_score DESC"
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
            "SELECT id, job_id, version, file_path, approved, grounded, source, "
            "created_at FROM resumes WHERE job_id = ? ORDER BY version DESC",
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


def save_uploaded_resume(job_id: int, data: bytes, ext: str = ".docx") -> tuple[int, int]:
    """Attach an EXISTING resume file (not tailored by the pipeline) to a job as a
    new version. Shares the version sequence with tailored drafts; grounded/draft
    stay null since there's nothing to fact-check. Returns (resume_id, version)."""
    out_dir = RESUMES_DIR / str(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as c:
        version = c.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM resumes WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
        path = out_dir / f"v{version}{ext}"
        path.write_bytes(data)
        cur = c.execute(
            "INSERT INTO resumes (job_id, version, file_path, approved, source) "
            "VALUES (?, ?, ?, 0, 'uploaded')",
            (job_id, version, str(path)),
        )
        return cur.lastrowid, version


def delete_job(job_id: int) -> None:
    """Delete a job, its resume rows, and its rendered resume files on disk."""
    import shutil
    with sqlite3.connect(DB_PATH) as c:
        c.execute("DELETE FROM resumes WHERE job_id = ?", (job_id,))
        c.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    folder = RESUMES_DIR / str(job_id)
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)


def set_jd_text(job_id: int, jd_text: str) -> None:
    """Replace a job's description. Alert emails only carry a teaser, so the user
    pastes the full JD from the posting before tailoring; this stores it."""
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE jobs SET jd_text = ? WHERE id = ?", (jd_text, job_id))
