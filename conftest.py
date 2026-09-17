"""Pytest bootstrap. Runs before any test module is imported.

Two jobs:
  1. Put the project root on sys.path so tests can `import db`, `import schemas`,
     `from stages import tailor` regardless of how pytest is invoked.
  2. Set a dummy DEEPSEEK_API_KEY. `import llm` no longer raises without one (the
     key is resolved lazily now), so this is no longer load-bearing for imports —
     it is a TRIPWIRE. The tests monkeypatch every LLM call, so if a real network
     call ever slips through, it fails on a bogus key instead of quietly spending
     money against whatever key happens to be in the developer's environment.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["DEEPSEEK_API_KEY"] = "test-key-not-used"
