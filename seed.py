"""Boot helper for a fresh install.

A clone has no personal data (profile.md / fact_bank.md are gitignored), so this
copies the committed *.example.md templates into place on first boot. It is a
NO-OP on a real setup (real files already present), so it never overwrites
anything.

Why copy the templates rather than leave the files missing: the Setup screen
shows them as a starting point to edit, and settings._read_doc() compares the
live file against its template to tell "you filled this in" from "this is still
the example". A fit score against the demo persona looks completely normal while
meaning nothing, so that distinction is what the setup gate checks.

IMPORTANT: this module must not import the stage modules. Call ensure_files()
BEFORE importing those.
"""
from __future__ import annotations

from paths import ROOT, PROFILE_FILE, FACT_BANK_FILE

_DATA = ROOT / "data"


def ensure_files() -> None:
    for path, example in (
        (PROFILE_FILE, _DATA / "profile.example.md"),
        (FACT_BANK_FILE, _DATA / "fact_bank.example.md"),
    ):
        if path.exists() or not example.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
