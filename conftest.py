"""Pytest bootstrap. Runs before any test module is imported.

Two jobs:
  1. Put the project root on sys.path so tests can `import db`, `import schemas`,
     `from stages import tailor` regardless of how pytest is invoked.
  2. Guarantee a DEEPSEEK_API_KEY exists so `import llm` (which raises without one)
     succeeds. The tests never hit the network — every LLM call is monkeypatched —
     so a dummy value is correct here.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-not-used")
