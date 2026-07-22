"""Integration tests for the tailor stage (Phase A + Phase B together).

These drive the WHOLE flow — get_job -> draft -> grounding check -> persist —
with the LLM boundary monkeypatched, so they run offline, deterministically, and
for free. The real API is exercised separately via `python -m stages.tailor`.

What's faked: `stages.tailor.call_structured` (the only outbound call). What's
real: claim collection, index->claim resolution, conservative flagging, DB
migration, versioning, and file persistence.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

import pytest

import db
from schemas import (Job, ResumeDraft, ResumeProject, SkillLine, ExpItem,
                     GroundingReport, ClaimVerdict)
from stages import tailor


# A canned draft standing in for the drafting LLM. One bullet is deliberately an
# "invented" claim so the grounding fake can flag exactly one thing.
CANNED_DRAFT = ResumeDraft(
    tagline="Data Science",
    profile="A tailored profile.",
    projects=[ResumeProject(
        title="Forecast Study", angle="Rigour", year="2026",
        stack="Python, XGBoost", url="github.com/x",
        bullets=["Ran walk-forward cross-validation.",
                 "Invented a metric not in the fact bank."])],
    skills=[SkillLine(label="ML", content="ridge, XGBoost")],
    education="UNSW BDatSci",
    experience=[ExpItem(role="Tutor", org="ABC College", dates="2022-2024",
                        bullets=["Taught 20+ students."])],
)


def _grounding_for(draft: ResumeDraft, flag_substr: str,
                   drop_index: int | None) -> GroundingReport:
    """Build a verdict list the way a checker would: flag any claim containing
    `flag_substr` (case-insensitive); optionally omit a verdict for `drop_index`
    to exercise the conservative-flagging path."""
    claims = tailor._collect_claims(draft)
    verdicts = []
    for i, claim in enumerate(claims):
        if i == drop_index:
            continue
        supported = flag_substr.lower() not in claim.lower()
        verdicts.append(ClaimVerdict(
            index=i, supported=supported,
            evidence="fact-bank line" if supported else ""))
    return GroundingReport(verdicts=verdicts)


@pytest.fixture
def fake_llm(monkeypatch):
    """Patch the tailor's only outbound call. Returns (cfg, calls) so a test can
    tune what the fake returns and inspect how it was called."""
    cfg = {"draft": CANNED_DRAFT, "flag_substr": "Invented", "drop_index": None}
    calls: list[dict] = []

    def fake_call_structured(system, user, schema, tier="cheap",
                             retries=2, temperature=0.0):
        calls.append({"schema": schema, "tier": tier, "temperature": temperature})
        if schema is ResumeDraft:
            return cfg["draft"].model_copy(deep=True)
        if schema is GroundingReport:
            return _grounding_for(cfg["draft"], cfg["flag_substr"], cfg["drop_index"])
        raise AssertionError(f"unexpected schema: {schema}")

    monkeypatch.setattr(tailor, "call_structured", fake_call_structured)
    return cfg, calls


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Redirect the DB and resume output to a temp dir, init the schema, and seed
    one job. Yields its job_id. Nothing touches the real applications.db."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "RESUMES_DIR", tmp_path / "resumes")
    db.init()
    job_id = db.upsert_job("email-1", Job(
        source="seek-alert", company="ACME", title="Data Scientist",
        jd_text="Do rigorous data science."))
    return job_id


def _resume_row(resume_id: int) -> dict:
    with sqlite3.connect(db.DB_PATH) as c:
        c.row_factory = sqlite3.Row
        return dict(c.execute(
            "SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone())


def _resume_rows_for_job(job_id: int) -> list[dict]:
    with sqlite3.connect(db.DB_PATH) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(
            "SELECT * FROM resumes WHERE job_id = ?", (job_id,)).fetchall()]


# --- The A+B integration path -----------------------------------------------

def test_tailor_job_persists_and_flags_unsupported(temp_db, fake_llm):
    _cfg, calls = fake_llm
    result = tailor.tailor_job(temp_db)

    # Phase A: a v1 draft is persisted with its preview and the grounding report.
    assert result.version == 1
    json_path = Path(result.json_path)
    assert json_path.exists() and json_path.name == "v1.json"
    assert Path(result.md_path).exists()
    assert (json_path.parent / "v1.grounding.json").exists()

    # Phase B: the invented claim is flagged; the real ones pass.
    assert result.grounding.all_supported is False
    assert len(result.grounding.flagged) == 1
    assert "invented" in result.grounding.flagged[0].claim.lower()

    # The row is pending and marked not-grounded; the report is stored.
    row = _resume_row(result.resume_id)
    assert row["approved"] == 0
    assert row["grounded"] == 0
    assert row["grounding_json"]

    # The two calls used the intended tiers/temperatures.
    draft_call = next(c for c in calls if c["schema"] is ResumeDraft)
    ground_call = next(c for c in calls if c["schema"] is GroundingReport)
    assert (draft_call["tier"], draft_call["temperature"]) == ("quality", 0.3)
    assert (ground_call["tier"], ground_call["temperature"]) == ("quality", 0.0)


def test_all_supported_sets_grounded_true_but_not_approved(temp_db, fake_llm):
    cfg, _ = fake_llm
    cfg["flag_substr"] = "NOTHING_MATCHES"          # nothing gets flagged
    result = tailor.tailor_job(temp_db)

    assert result.grounding.all_supported is True
    assert result.grounding.flagged == []
    row = _resume_row(result.resume_id)
    assert row["grounded"] == 1
    assert row["approved"] == 0                     # human gate stays closed


def test_missing_verdict_is_conservatively_flagged(temp_db, fake_llm):
    cfg, _ = fake_llm
    cfg["flag_substr"] = "NOTHING_MATCHES"          # checker would pass everything...
    cfg["drop_index"] = 0                           # ...but returns no verdict for claim 0
    result = tailor.tailor_job(temp_db)

    flagged = result.grounding.flagged
    assert len(flagged) == 1
    assert flagged[0].evidence == "(no verdict returned)"
    assert result.grounding.all_supported is False


def test_versioning_increments_and_never_clobbers(temp_db, fake_llm):
    r1 = tailor.tailor_job(temp_db)
    r2 = tailor.tailor_job(temp_db)

    assert (r1.version, r2.version) == (1, 2)
    assert r1.json_path != r2.json_path
    assert Path(r1.json_path).exists() and Path(r2.json_path).exists()
    assert len(_resume_rows_for_job(temp_db)) == 2


def test_persisted_json_roundtrips_to_the_same_draft(temp_db, fake_llm):
    result = tailor.tailor_job(temp_db)
    reloaded = ResumeDraft.model_validate_json(
        Path(result.json_path).read_text(encoding="utf-8"))
    assert reloaded == result.draft


# --- Narrow unit checks the integration path leans on ------------------------

def test_collect_claims_covers_bullets_skills_education_not_profile():
    claims = tailor._collect_claims(CANNED_DRAFT)
    joined = "\n".join(claims)
    assert "[Project: Forecast Study]" in joined
    assert "[Experience: Tutor, ABC College]" in joined
    assert "[Skill] ML: ridge, XGBoost" in claims
    assert any(c.startswith("[Education]") for c in claims)
    # profile/tagline are framing, never sent to the checker
    assert "A tailored profile." not in joined
    assert "Data Science" not in joined  # the tagline text


def test_get_job_roundtrips(temp_db):
    job = db.get_job(temp_db)
    assert job.company == "ACME"
    assert job.title == "Data Scientist"


def test_get_job_missing_raises(temp_db):
    with pytest.raises(ValueError):
        db.get_job(999_999)
