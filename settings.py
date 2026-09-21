"""Runtime configuration — the things the Setup pane writes.

TWO stores, because the two kinds of value want different homes:

  API key   -> .env          Already gitignored, already what llm.py reads, and
                             already what the README and Privacy section promise.
                             A real environment variable still wins over the file
                             (load_dotenv does not override).
  Everything else -> data/settings.json   Structured, non-secret, validated
                             through the pydantic models in schemas.py.

Why not put the key in settings.json too: one file that is sometimes a secret and
sometimes not is a file people leak. Keeping secrets in exactly one gitignored
place that already existed is less to explain and less to get wrong.

Reads are NOT cached. The web app edits these at runtime, and a cached copy would
serve stale config until restart — which is the whole problem this module exists
to fix. The files are a few KB; a read per call is free next to an LLM round trip.

LEARN: configuration vs secrets, validated settings boundaries, why import-time
       constants make software unconfigurable.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import paths
from paths import FACT_BANK_FILE, PROFILE_FILE, ROOT
from schemas import AppSettings, EmailSource

SETTINGS_FILE = ROOT / "data" / "settings.json"

KEY_NAME = "DEEPSEEK_API_KEY"

# Example templates shipped in the repo. Used to tell "you filled this in" from
# "this is still the demo persona seed.ensure_files() dropped here".
PROFILE_EXAMPLE = ROOT / "data" / "profile.example.md"
FACT_BANK_EXAMPLE = ROOT / "data" / "fact_bank.example.md"


# --- structured settings -----------------------------------------------------

def load() -> AppSettings:
    """Current settings, or defaults.

    A corrupt or hand-mangled settings.json falls back to defaults rather than
    taking the app down: this file is edited by a form, the defaults are sane,
    and an unbootable app is a worse failure than a silently reset preference.
    (Contrast with the fact bank, where a silent default WOULD be worse — an
    empty fact bank yields a fabricated resume, so that one still raises.)
    """
    if not SETTINGS_FILE.exists():
        return AppSettings()
    try:
        return AppSettings.model_validate_json(
            SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return AppSettings()


def save(settings: AppSettings) -> AppSettings:
    """Persist settings. Written via a temp file + replace so an interrupted
    write cannot leave a half-written JSON file that load() then silently
    discards as corrupt."""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)
    return settings


# --- API key (.env) ----------------------------------------------------------

_KEY_LINE = re.compile(rf"^\s*(export\s+)?{KEY_NAME}\s*=", re.IGNORECASE)


def set_api_key(key: str) -> None:
    """Write the key into .env, preserving every other line.

    Rewrites only the DEEPSEEK_API_KEY line rather than regenerating the file,
    because .env is hand-edited and may hold comments or unrelated variables that
    a blind overwrite would destroy.

    Also updates os.environ: load_dotenv will not override an already-set
    variable, so without this the OLD key would keep winning for the life of the
    process and the Setup pane would look broken.
    """
    key = (key or "").strip()
    if not key:
        raise ValueError("API key is empty.")

    lines = (paths.ENV_FILE.read_text(encoding="utf-8").splitlines()
             if paths.ENV_FILE.exists() else [])
    new_line = f"{KEY_NAME}={key}"
    replaced = False
    for i, line in enumerate(lines):
        if _KEY_LINE.match(line):
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        lines.append(new_line)

    paths.ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[KEY_NAME] = key

    # Drop llm.py's cached client so the next call uses the key just written.
    # Imported here, not at module top: settings.py must stay importable without
    # a configured key (api.py imports it during boot).
    import llm
    llm.reset_client()


def api_key_status() -> dict:
    """What the Setup pane shows about the key: whether one is set, a masked
    preview, and where it came from. Never returns the key itself — it is a
    secret, and the pane only needs to confirm which one is loaded.

    `source` matters: when the key comes from a real environment variable
    (set in the shell or system settings), writing .env will NOT change what the app uses, so the
    UI has to say so instead of silently no-opping.
    """
    import llm
    key = llm._api_key()
    if not key:
        return {"configured": False, "masked": "", "source": None}

    in_env_file = False
    if paths.ENV_FILE.exists():
        in_env_file = any(_KEY_LINE.match(ln)
                          for ln in paths.ENV_FILE.read_text(encoding="utf-8").splitlines())
    # An env var set outside the file wins; say which one is actually live.
    from dotenv import dotenv_values
    file_val = dotenv_values(paths.ENV_FILE).get(KEY_NAME) if paths.ENV_FILE.exists() else None
    source = "env-file" if (in_env_file and file_val == key) else "environment"

    masked = f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "set"
    return {"configured": True, "masked": masked, "source": source}


# --- profile / fact bank markdown -------------------------------------------

def _read_doc(path: Path, example: Path) -> dict:
    """One personal-data markdown file, plus whether it is still the shipped
    example. seed.ensure_files() copies the examples in on a bare deploy, so a
    file existing does NOT mean you have filled it in — and a fit score against
    the demo persona looks completely normal while meaning nothing."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    is_example = False
    if text and example.exists():
        is_example = text.strip() == example.read_text(encoding="utf-8").strip()
    return {"content": text, "exists": path.exists(), "is_example": is_example}


def _write_doc(path: Path, content: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return len(content)


def get_profile() -> dict:
    return _read_doc(PROFILE_FILE, PROFILE_EXAMPLE)


def set_profile(content: str) -> int:
    return _write_doc(PROFILE_FILE, content)


def get_fact_bank() -> dict:
    return _read_doc(FACT_BANK_FILE, FACT_BANK_EXAMPLE)


def set_fact_bank(content: str) -> int:
    return _write_doc(FACT_BANK_FILE, content)


# --- email sources -----------------------------------------------------------

# The senders the app shipped with, before the list was configurable. Still the
# fallback when nothing is configured, so behaviour is unchanged until you set up
# your own — a fresh install ingests exactly what it did before.
DEFAULT_SOURCES = [
    "jobalerts-noreply@linkedin.com",
    "jobs-noreply@linkedin.com",
    "jobs-listings@linkedin.com",
    "notifications@us.greenhouse-jobs.com",
    "seek.com.au",
    "indeed.com",
    "jobs2web.com",
]


def default_sources() -> list[EmailSource]:
    return [EmailSource(value=v, origin="default") for v in DEFAULT_SOURCES]


def sender_values() -> list[str]:
    """The enabled senders for the Gmail query, falling back to the built-in list.

    The fallback is deliberate: an empty list would build `from:()` and match
    NOTHING, so an unconfigured install would silently ingest zero emails and
    look like the pipeline was broken.
    """
    s = load()
    enabled = s.enabled_sources()
    return enabled or list(DEFAULT_SOURCES)


def set_sources(sources: list[EmailSource]) -> AppSettings:
    """Replace the sender list, de-duplicated by normalised value (first wins, so
    the order the UI sent them in is preserved)."""
    seen: set[str] = set()
    unique: list[EmailSource] = []
    for src in sources:
        if src.value in seen:
            continue
        seen.add(src.value)
        unique.append(src)

    s = load()
    s.email_sources = unique
    s.sources_configured = True
    return save(s)
