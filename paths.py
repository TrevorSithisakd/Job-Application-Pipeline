"""Every file path in the project resolves from here — never from the cwd.

Why this exists: `Path("data/profile.md")` is relative to wherever you launched
python from, not to the code. Run the pipeline from another directory and those
reads silently return nothing instead of raising, so a fit score gets computed
against a BLANK profile and looks perfectly normal. Anchoring to __file__ makes
the location independent of how you invoke it.

LEARN: cwd vs module location, why silent fallbacks are worse than crashes.
"""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DB_PATH = ROOT / "applications.db"
ENV_FILE = ROOT / ".env"

# Google OAuth: you supply credentials.json; token.json is written on first auth.
CREDENTIALS_FILE = ROOT / "credentials.json"
TOKEN_FILE = ROOT / "token.json"

# Personal data (gitignored) — read as context by the fit-score and resume stages.
PROFILE_FILE = ROOT / "data" / "profile.md"
FACT_BANK_FILE = ROOT / "data" / "fact_bank.md"

# Generated resume drafts (gitignored): data/resumes/<job_id>/v<n>.json + .md
RESUMES_DIR = ROOT / "data" / "resumes"
