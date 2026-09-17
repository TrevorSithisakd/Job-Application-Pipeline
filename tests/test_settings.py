"""Tests for the Setup layer: the settings store, the .env key writer, and the
rubric the fit-score prompt is composed from.

No network. The only LLM boundary touched here is `call_structured`, and it is
monkeypatched the same way the other test modules do it.

What's real: pydantic validation, the JSON round trip, the atomic write, the .env
line rewriting, and the prompt composition — i.e. everything that decides whether
a preference you typed actually reaches the model.
"""
from __future__ import annotations

import pytest

import paths
import settings
from schemas import (AppSettings, EmailSource, JobPreferences, RubricDimension,
                     ScoringRubric)
from stages import fitscore


@pytest.fixture(autouse=True)
def temp_settings(tmp_path, monkeypatch):
    """Point every settings path at a temp dir. Without this the suite would
    read and overwrite the developer's real .env and data/settings.json."""
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(paths, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(settings, "PROFILE_FILE", tmp_path / "profile.md")
    monkeypatch.setattr(settings, "FACT_BANK_FILE", tmp_path / "fact_bank.md")
    monkeypatch.setattr(settings, "PROFILE_EXAMPLE", tmp_path / "profile.example.md")
    monkeypatch.setattr(settings, "FACT_BANK_EXAMPLE", tmp_path / "fact_bank.example.md")
    return tmp_path


# --- store round trip --------------------------------------------------------

def test_load_returns_defaults_when_no_file():
    s = settings.load()
    assert s.preferences.tracks           # a default track list, not empty
    assert s.rubric.strong_min == 75
    assert s.email_sources == []
    assert s.sources_configured is False


def test_save_then_load_round_trips():
    s = AppSettings()
    s.preferences = JobPreferences(locations=["Sydney"], salary_floor=110_000,
                                   must_have_skills=["Python"])
    s.rubric = ScoringRubric(strong_min=85, possible_min=60,
                             extra_criteria="Must ship to production.")
    settings.save(s)

    back = settings.load()
    assert back.preferences.locations == ["Sydney"]
    assert back.preferences.salary_floor == 110_000
    assert back.rubric.strong_min == 85
    assert back.rubric.extra_criteria == "Must ship to production."


def test_corrupt_settings_file_falls_back_to_defaults(temp_settings):
    """A hand-mangled settings.json must not take the app down. The defaults are
    sane and the file is form-edited, so an unbootable app is the worse failure.
    (The fact bank deliberately does NOT behave this way — see test below.)"""
    settings.SETTINGS_FILE.write_text("{not json at all", encoding="utf-8")
    s = settings.load()
    assert s.rubric.strong_min == 75


def test_save_is_atomic_and_leaves_no_temp_file(temp_settings):
    settings.save(AppSettings())
    assert settings.SETTINGS_FILE.exists()
    assert not list(temp_settings.glob("*.tmp"))


# --- validation --------------------------------------------------------------

def test_bands_must_be_ordered():
    """An inverted band pair makes the middle band empty, so every job is either
    strong or rejected — silently useless rather than obviously wrong."""
    with pytest.raises(ValueError):
        ScoringRubric(strong_min=50, possible_min=70)


def test_empty_track_list_falls_back_to_defaults():
    assert JobPreferences(tracks=["  ", ""]).tracks == JobPreferences().tracks


def test_all_tracks_always_offers_none():
    prefs = JobPreferences(tracks=["ml-engineer"])
    assert prefs.all_tracks() == ["ml-engineer", "none"]


@pytest.mark.parametrize("raw,expected", [
    ("SEEK <Jobs@S.Seek.COM.AU>", "jobs@s.seek.com.au"),
    ("  LinkedIn.com  ", "linkedin.com"),
    ("@indeed.com", "indeed.com"),
])
def test_email_source_normalises(raw, expected):
    assert EmailSource(value=raw).value == expected


# --- sender list -------------------------------------------------------------

def test_sender_values_falls_back_to_defaults():
    """An empty list would build `from:()` and match NOTHING, so an unconfigured
    install would ingest zero emails and look broken."""
    assert settings.sender_values() == settings.DEFAULT_SOURCES


def test_set_sources_dedupes_after_normalising():
    saved = settings.set_sources([
        EmailSource(value="SEEK <jobs@s.seek.com.au>", origin="scan"),
        EmailSource(value="jobs@s.seek.com.au", origin="manual"),   # same thing
        EmailSource(value="linkedin.com", origin="manual"),
    ])
    assert [e.value for e in saved.email_sources] == ["jobs@s.seek.com.au", "linkedin.com"]
    assert saved.sources_configured is True
    # First wins, so the order the UI sent survives.
    assert saved.email_sources[0].origin == "scan"


def test_disabled_sources_are_excluded_from_the_query():
    settings.set_sources([
        EmailSource(value="seek.com.au", enabled=True),
        EmailSource(value="indeed.com", enabled=False),
    ])
    assert settings.sender_values() == ["seek.com.au"]


def test_disabling_every_source_falls_back_rather_than_matching_nothing():
    settings.set_sources([EmailSource(value="seek.com.au", enabled=False)])
    assert settings.sender_values() == settings.DEFAULT_SOURCES


# --- .env key writer ---------------------------------------------------------

def test_set_api_key_preserves_other_lines(temp_settings, monkeypatch):
    """.env is hand-edited and may hold comments and unrelated variables, so the
    writer rewrites one line rather than regenerating the file."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    paths.ENV_FILE.write_text(
        "# my notes\nOTHER_VAR=keep-me\nDEEPSEEK_API_KEY=sk-old\nTRAILING=yes\n",
        encoding="utf-8")

    settings.set_api_key("sk-new")

    text = paths.ENV_FILE.read_text(encoding="utf-8")
    assert "# my notes" in text
    assert "OTHER_VAR=keep-me" in text
    assert "TRAILING=yes" in text
    assert "DEEPSEEK_API_KEY=sk-new" in text
    assert "sk-old" not in text


def test_set_api_key_appends_when_absent(temp_settings, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    paths.ENV_FILE.write_text("OTHER=1\n", encoding="utf-8")
    settings.set_api_key("sk-fresh")
    text = paths.ENV_FILE.read_text(encoding="utf-8")
    assert "OTHER=1" in text and "DEEPSEEK_API_KEY=sk-fresh" in text


def test_set_api_key_creates_the_file(temp_settings, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    settings.set_api_key("sk-created")
    assert "DEEPSEEK_API_KEY=sk-created" in paths.ENV_FILE.read_text(encoding="utf-8")


def test_set_api_key_updates_the_live_process(monkeypatch):
    """load_dotenv never overrides an already-set variable, so without this the
    OLD key would keep winning for the life of the process and the Setup pane
    would look broken."""
    import os
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-stale")
    settings.set_api_key("sk-live")
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-live"


def test_set_api_key_rejects_empty():
    with pytest.raises(ValueError):
        settings.set_api_key("   ")


def test_env_path_has_exactly_one_binding(temp_settings, monkeypatch):
    """Guard against a whole class of bug that shipped once already.

    llm.py and settings.py must both reach .env through the `paths` MODULE. When
    either took its own `from paths import ENV_FILE` copy, patching one left the
    other pointing at the developer's real .env — so every "no API key" test
    quietly read a live key and passed for the wrong reason on a machine that had
    one. Writing through settings must be visible to llm, and only there.
    """
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    import llm

    assert not hasattr(settings, "ENV_FILE"), \
        "settings must not shadow paths.ENV_FILE with its own binding"
    assert not hasattr(llm, "ENV_FILE"), \
        "llm must not shadow paths.ENV_FILE with its own binding"

    settings.set_api_key("sk-written-here")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)   # force a file read
    assert llm._api_key() == "sk-written-here"

    # And with the temp file pointed elsewhere, no key is visible — proving the
    # read really followed the patched path rather than a real .env on disk.
    monkeypatch.setattr(paths, "ENV_FILE", temp_settings / "nonexistent.env")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert llm._api_key() is None


def test_api_key_status_never_leaks_the_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-abcdef1234567890")
    status = settings.api_key_status()
    assert status["configured"] is True
    assert "abcdef1234567890" not in status["masked"]


# --- profile / fact bank docs ------------------------------------------------

def test_is_example_flags_the_unmodified_demo_persona(temp_settings):
    """seed.ensure_files() copies the examples in on a bare deploy, so a file
    EXISTING does not mean it has been filled in — and scoring against the demo
    persona looks completely normal while meaning nothing."""
    settings.PROFILE_EXAMPLE.write_text("# example\n- Name: Jane Doe\n", encoding="utf-8")
    settings.PROFILE_FILE.write_text("# example\n- Name: Jane Doe\n", encoding="utf-8")
    assert settings.get_profile()["is_example"] is True

    settings.set_profile("# mine\n- Name: A Real Person\n")
    assert settings.get_profile()["is_example"] is False


def test_missing_doc_reports_absent_not_empty():
    d = settings.get_profile()
    assert d["exists"] is False and d["content"] == ""


# --- rubric composition ------------------------------------------------------

def test_weights_render_as_normalised_percentages():
    """Raw sliders summing to 260 read as arbitrary magnitudes to a model;
    percentages are a directly usable instruction."""
    rubric = ScoringRubric(dimensions=[
        RubricDimension(key="a", label="Alpha", weight=30),
        RubricDimension(key="b", label="Beta", weight=10),
    ])
    out = fitscore.render_weights(rubric)
    assert "Alpha: 75% of the score" in out
    assert "Beta: 25% of the score" in out


def test_zero_weight_dimension_is_dropped_entirely():
    rubric = ScoringRubric(dimensions=[
        RubricDimension(key="a", label="Alpha", weight=50),
        RubricDimension(key="b", label="Ignored", weight=0),
    ])
    assert "Ignored" not in fitscore.render_weights(rubric)


def test_all_zero_weights_degrade_to_equal_weighting():
    """Otherwise this divides by zero on a perfectly reachable slider state."""
    rubric = ScoringRubric(dimensions=[RubricDimension(key="a", label="Alpha", weight=0)])
    assert "equally" in fitscore.render_weights(rubric)


def test_empty_preferences_are_omitted_not_sent_as_blanks():
    """A blank constraint reads to the model as a real one and skews the score."""
    out = fitscore.render_preferences(JobPreferences())
    assert "Preferred locations" not in out
    assert "Dealbreakers" not in out
    assert "No specific preferences set" in out


def test_preferences_reach_the_prompt():
    prefs = JobPreferences(locations=["Sydney", "Remote AU"], salary_floor=110_000,
                           must_have_skills=["Python"], dealbreakers=["clearance"],
                           remote_policy="hybrid", tracks=["ml-engineer"])
    prompt = fitscore.build_rubric(prefs, ScoringRubric())
    assert "Sydney, Remote AU" in prompt
    assert "110,000 AUD" in prompt
    assert "Python" in prompt
    assert "clearance" in prompt
    assert "prefers hybrid" in prompt
    assert "ml-engineer | none" in prompt


def test_bands_and_free_text_reach_the_prompt():
    rubric = ScoringRubric(strong_min=85, possible_min=60,
                           band_notes="85+ means drop everything.",
                           extra_criteria="Penalise pure consultancies.")
    prompt = fitscore.build_rubric(JobPreferences(), rubric)
    assert "85-100: strong match" in prompt
    assert "60-84: possible" in prompt
    assert "0-59: weak match" in prompt
    assert "85+ means drop everything." in prompt
    assert "Penalise pure consultancies." in prompt


def test_build_rubric_reads_saved_settings_when_not_given_any():
    s = AppSettings()
    s.preferences = JobPreferences(locations=["Melbourne"])
    settings.save(s)
    assert "Melbourne" in fitscore.build_rubric()


# --- track validation moved out of the schema --------------------------------

def test_off_list_track_is_coerced_to_none(monkeypatch, tmp_path):
    """FitScore.track can no longer be a Literal (tracks are configurable), so
    fitscore validates it instead. An invented category would fragment the
    board's grouping, so it falls back to "none"."""
    from schemas import FitScore, Job

    s = AppSettings()
    s.preferences = JobPreferences(tracks=["ml-engineer"])
    settings.save(s)
    monkeypatch.setattr(fitscore, "profile", lambda: "a profile")
    monkeypatch.setattr(fitscore, "call_structured", lambda *a, **k: FitScore(
        score=70, rationale="ok", track="quantum-alchemist"))

    result = fitscore.fit_score(Job(source="x", company="C", title="T", jd_text="jd"))
    assert result.track == "none"


def test_configured_track_survives_and_is_normalised(monkeypatch):
    from schemas import FitScore, Job

    s = AppSettings()
    s.preferences = JobPreferences(tracks=["ML-Engineer"])
    settings.save(s)
    monkeypatch.setattr(fitscore, "profile", lambda: "a profile")
    monkeypatch.setattr(fitscore, "call_structured", lambda *a, **k: FitScore(
        score=70, rationale="ok", track="  ml-engineer  "))

    result = fitscore.fit_score(Job(source="x", company="C", title="T", jd_text="jd"))
    assert result.track == "ML-Engineer"      # mapped back to the configured casing
