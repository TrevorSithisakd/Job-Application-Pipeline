"""Tests for multi-job extraction and per-posting (URL) dedup.

The extract LLM call is monkeypatched; the DB logic is real. Covers the two
digest-era changes: one email -> many jobs, and idempotency keyed on each job's
URL (falling back to email_id:title) rather than on the email.
"""
from __future__ import annotations
import sqlite3

import pytest

import db
from schemas import Job, JobList
from stages import extract


def _job(title: str, url: str | None = None, company: str = "Co") -> Job:
    return Job(source="seek-alert", company=company, title=title,
               jd_text="teaser", url=url)


def _count() -> int:
    with sqlite3.connect(db.DB_PATH) as c:
        return c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init()


# --- Multi-job extraction ----------------------------------------------------

def test_extract_returns_all_jobs_in_a_digest(monkeypatch):
    jl = JobList(jobs=[_job("A", "u1"), _job("B", "u2"), _job("C", "u3")])
    monkeypatch.setattr(extract, "call_structured", lambda *a, **k: jl)
    jobs = extract.extract("digest body with three roles")
    assert [j.title for j in jobs] == ["A", "B", "C"]


def test_extract_returns_empty_for_non_posting(monkeypatch):
    monkeypatch.setattr(extract, "call_structured", lambda *a, **k: JobList(jobs=[]))
    assert extract.extract("a newsletter, not a posting") == []


# --- Dedup on normalized company+title (not url) -----------------------------

def test_same_job_across_platforms_and_emails_dedups(temp_db):
    # Same posting via LinkedIn and via Indeed (different urls, different emails,
    # different case/spacing) must collapse to one row.
    id1 = db.upsert_job("email-A", _job("Data Scientist", "https://linkedin.com/jobs/view/1", company="CBA"))
    id2 = db.upsert_job("email-B", _job("data scientist", "https://indeed.com/pagead?ad=zzz", company="  CBA "))
    assert id1 == id2
    assert _count() == 1


def test_different_titles_same_company_are_distinct(temp_db):
    a = db.upsert_job("e", _job("Data Scientist", "u1", company="CBA"))
    b = db.upsert_job("e", _job("Data Engineer", "u2", company="CBA"))
    assert a != b
    assert _count() == 2


def test_same_title_different_company_are_distinct(temp_db):
    a = db.upsert_job("e", _job("Data Scientist", "u1", company="CBA"))
    b = db.upsert_job("e", _job("Data Scientist", "u2", company="NAB"))
    assert a != b
    assert _count() == 2
