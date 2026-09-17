"""End-to-end tests for the Setup endpoints, through the real FastAPI app.

The headline case is `test_app_boots_without_an_api_key`. Before the lazy-config
change, importing `api` with no key raised at import time: the server could not
start, so there was no way to reach a page that sets the key. That was the
chicken-and-egg this whole feature rests on, and it deserves a regression test.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

import api
import db
import llm
import settings
from schemas import FactBankDoc, FactItem, FactSection
from stages import factbank, ingest


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fully isolated app: temp DB, temp settings, temp .env, no real key."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(settings, "PROFILE_FILE", tmp_path / "profile.md")
    monkeypatch.setattr(settings, "FACT_BANK_FILE", tmp_path / "fact_bank.md")
    monkeypatch.setattr(settings, "PROFILE_EXAMPLE", tmp_path / "profile.example.md")
    monkeypatch.setattr(settings, "FACT_BANK_EXAMPLE", tmp_path / "fact_bank.example.md")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_client", None)
    db.init()
    return TestClient(api.app)


# --- the blocker this feature rests on ---------------------------------------

def test_app_boots_without_an_api_key(client, monkeypatch):
    """Regression: `import llm` used to raise without a key, taking api.py down
    with it — so the server could not start and the Setup page was unreachable."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_client", None)
    assert llm.has_key() is False

    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["needs"]["key"] is True


def test_calls_still_fail_loudly_without_a_key(monkeypatch):
    """Deferring the check must not weaken it — a stage still fails immediately
    and says why, just at call time rather than import time."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm, "_api_key", lambda: None)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        llm._get_client()


# --- settings surface --------------------------------------------------------

def test_settings_never_returns_the_key_itself(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-supersecret-value-here")
    body = client.get("/api/settings").json()
    assert "sk-supersecret-value-here" not in str(body)
    assert body["key"]["configured"] is True


def test_needs_flags_drive_the_first_run_banner(client):
    needs = client.get("/api/settings").json()["needs"]
    assert needs == {"key": True, "profile": True, "fact_bank": True, "sources": True}


def test_saving_a_key_writes_env_and_clears_the_cached_client(client, monkeypatch):
    monkeypatch.setattr(llm, "_client", "stale-sentinel")
    r = client.post("/api/settings/key", json={"key": "sk-brand-new"})
    assert r.status_code == 200
    assert "DEEPSEEK_API_KEY=sk-brand-new" in settings.ENV_FILE.read_text(encoding="utf-8")
    assert llm._client is None, "cached client must be dropped or the old key keeps winning"


def test_empty_key_is_rejected(client):
    assert client.post("/api/settings/key", json={"key": "  "}).status_code == 400


def test_key_test_reports_failure_without_raising(client, monkeypatch):
    def boom():
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(llm, "smoke_test", boom)
    body = client.post("/api/settings/key/test").json()
    assert body["ok"] is False and "401" in body["error"]


def test_key_test_reports_success(client, monkeypatch):
    monkeypatch.setattr(llm, "smoke_test", lambda: "OK")
    assert client.post("/api/settings/key/test").json() == {"ok": True, "reply": "OK"}


# --- preferences and rubric --------------------------------------------------

def test_preferences_round_trip(client):
    r = client.put("/api/settings/preferences", json={
        "locations": ["Sydney"], "salary_floor": 110000,
        "must_have_skills": ["Python"], "remote_policy": "hybrid"})
    assert r.status_code == 200
    assert client.get("/api/settings").json()["preferences"]["locations"] == ["Sydney"]


def test_invalid_remote_policy_is_a_422(client):
    r = client.put("/api/settings/preferences", json={"remote_policy": "telepathic"})
    assert r.status_code == 422


def test_inverted_bands_are_rejected(client):
    r = client.put("/api/settings/rubric",
                   json={"strong_min": 50, "possible_min": 70})
    assert r.status_code == 422


def test_rubric_preview_reflects_unsaved_sliders(client):
    """The preview is what makes the sliders inspectable rather than a black box,
    so it must render the POSTed values, not the saved ones."""
    client.put("/api/settings/preferences", json={"locations": ["Perth"]})
    body = client.post("/api/settings/rubric/preview", json={
        "dimensions": [{"key": "skills", "label": "Skills", "weight": 90},
                       {"key": "salary", "label": "Salary", "weight": 10}],
        "strong_min": 88, "possible_min": 44,
        "extra_criteria": "Remote-first only."}).json()

    assert "Skills: 90% of the score" in body["prompt"]
    assert "88-100: strong match" in body["prompt"]
    assert "Remote-first only." in body["prompt"]
    assert "Perth" in body["prompt"], "saved preferences should still appear"
    # Preview must not persist.
    assert client.get("/api/settings").json()["rubric"]["strong_min"] == 75


def test_empty_rubric_body_resets_to_defaults(client):
    client.put("/api/settings/rubric", json={"strong_min": 90, "possible_min": 30,
                                             "extra_criteria": "x"})
    client.put("/api/settings/rubric", json={})
    saved = client.get("/api/settings").json()["rubric"]
    assert saved["strong_min"] == 75 and saved["extra_criteria"] == ""


# --- profile / fact bank -----------------------------------------------------

def test_profile_and_factbank_round_trip(client):
    client.put("/api/settings/profile", json={"content": "# me\n- Python"})
    assert client.get("/api/settings/profile").json()["content"] == "# me\n- Python"

    client.put("/api/settings/factbank", json={"content": "- Used pandas."})
    assert client.get("/api/settings/factbank").json()["content"] == "- Used pandas."
    assert client.get("/api/settings").json()["needs"]["fact_bank"] is False


def _docx_bytes(text="Jane Doe\nUsed scikit-learn."):
    import docx
    d = docx.Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_factbank_extract_returns_markdown_without_saving(client, monkeypatch):
    """The grounding check treats the fact bank as ground truth, so an LLM
    writing that file unreviewed would let an extraction error certify itself as
    a verified resume claim. A human presses Save."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(factbank, "call_structured", lambda *a, **k: FactBankDoc(
        sections=[FactSection(heading="Skills", items=[FactItem(text="Python")])]))

    r = client.post("/api/settings/factbank/extract",
                    files={"file": ("cv.docx", _docx_bytes(), "application/octet-stream")})
    assert r.status_code == 200
    assert r.json()["saved"] is False
    assert "- Python" in r.json()["content"]
    # Nothing written to disk.
    assert client.get("/api/settings/factbank").json()["content"] == ""


def test_factbank_extract_rejects_wrong_file_type(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    r = client.post("/api/settings/factbank/extract",
                    files={"file": ("cv.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_factbank_extract_without_a_key_is_a_clear_409(client, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_client", None)
    r = client.post("/api/settings/factbank/extract",
                    files={"file": ("cv.docx", _docx_bytes(), "application/octet-stream")})
    assert r.status_code == 409
    assert "DEEPSEEK_API_KEY" in r.json()["detail"]


# --- email sources -----------------------------------------------------------

def test_senders_endpoint_shows_the_fallback_before_configuration(client):
    body = client.get("/api/settings/senders").json()
    assert body["configured"] is False
    assert body["effective"] == settings.DEFAULT_SOURCES


def test_saving_senders_normalises_dedupes_and_marks_configured(client):
    r = client.put("/api/settings/senders", json={"sources": [
        {"value": "SEEK <Jobs@S.Seek.COM.AU>", "enabled": True, "origin": "scan"},
        {"value": "jobs@s.seek.com.au", "enabled": True, "origin": "manual"},
        {"value": "indeed.com", "enabled": False, "origin": "manual"}]})
    assert [s["value"] for s in r.json()["sources"]] == ["jobs@s.seek.com.au", "indeed.com"]
    assert r.json()["effective"] == ["jobs@s.seek.com.au"]
    assert client.get("/api/settings").json()["needs"]["sources"] is False


def test_scan_requires_gmail_authorisation(client, monkeypatch):
    """A browser fetch cannot complete Google's consent flow, so this has to be a
    clear message rather than a hang or a stack trace."""
    monkeypatch.setattr(ingest, "credentials_ready", lambda: False)
    r = client.post("/api/settings/senders/scan", json={"days": 90})
    assert r.status_code == 409
    assert "Gmail authorization needed" in r.json()["detail"]


def test_scan_runs_and_reports_results(client, monkeypatch):
    monkeypatch.setattr(ingest, "credentials_ready", lambda: True)
    monkeypatch.setattr(ingest, "discover_senders", lambda **kw: [
        {"domain": "seek.com.au", "address": "a@seek.com.au", "display_name": "SEEK",
         "count": 4, "sample_subjects": ["jobs"], "confidence": "heuristic",
         "already_added": False}])

    assert client.post("/api/settings/senders/scan", json={"days": 90}).json()["started"]
    for _ in range(100):                       # the worker is a real thread
        status = client.get("/api/settings/senders/scan/status").json()
        if status["done"]:
            break
    assert status["error"] is None
    assert status["senders"][0]["domain"] == "seek.com.au"


def test_scan_surfaces_a_worker_error_instead_of_hanging(client, monkeypatch):
    monkeypatch.setattr(ingest, "credentials_ready", lambda: True)

    def boom(**kw):
        raise RuntimeError("gmail exploded")

    monkeypatch.setattr(ingest, "discover_senders", boom)
    client.post("/api/settings/senders/scan", json={"days": 90})
    for _ in range(100):
        status = client.get("/api/settings/senders/scan/status").json()
        if status["done"]:
            break
    assert "gmail exploded" in status["error"]
    assert status["running"] is False


# --- rescore -----------------------------------------------------------------

def _seed_job(company="ACME", scored=True):
    # Distinct companies matter: db.upsert_job dedupes on normalised
    # company + title, so two identical seeds would collapse into one row.
    from schemas import FitScore, Job
    jid = db.upsert_job("e1", Job(source="manual", company=company,
                                  title="Data Scientist", jd_text="jd"))
    if scored:
        db.set_fit(jid, FitScore(score=50, rationale="meh", track="none"))
    return jid


def test_rescore_estimate_counts_only_scored_jobs(client):
    _seed_job("ACME", scored=True)
    _seed_job("Globex", scored=False)
    assert client.get("/api/jobs/rescore/estimate?only_scored=true").json()["jobs"] == 1
    assert client.get("/api/jobs/rescore/estimate?only_scored=false").json()["jobs"] == 2


def test_rescore_without_a_key_is_refused_up_front(client, monkeypatch):
    _seed_job()
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_client", None)
    assert client.post("/api/jobs/rescore", json={"only_scored": True}).status_code == 409


def test_rescore_applies_the_new_criteria(client, monkeypatch):
    from schemas import FitScore
    jid = _seed_job()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(api.fitscore, "fit_score", lambda job: FitScore(
        score=91, rationale="now a strong match", track="none"))

    client.post("/api/jobs/rescore", json={"only_scored": True})
    for _ in range(100):
        status = client.get("/api/jobs/rescore/status").json()
        if status["done"]:
            break
    assert status["error"] is None and status["scored"] == 1
    assert db.get_job_row(jid)["fit_score"] == 91


def test_one_bad_job_does_not_abandon_the_batch(client, monkeypatch):
    """Same isolation as pipeline.run(): a thin JD or a transient API error must
    not discard the rest of the run."""
    from schemas import FitScore
    _seed_job("Breaks Inc")
    _seed_job("Works Inc")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    def flaky(job):
        if job.company == "Breaks Inc":
            raise RuntimeError("transient")
        return FitScore(score=88, rationale="fine", track="none")

    monkeypatch.setattr(api.fitscore, "fit_score", flaky)
    client.post("/api/jobs/rescore", json={"only_scored": True})
    for _ in range(100):
        status = client.get("/api/jobs/rescore/status").json()
        if status["done"]:
            break
    assert status["scored"] == 1 and status["skipped"] == 1
