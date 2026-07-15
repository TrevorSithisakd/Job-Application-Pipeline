"""ONE inference wrapper. Every stage calls through here.

Why one place: model choice, temperature, retries, and JSON-repair live in
a single file. Swap providers or models without touching any stage.

LEARN: LLM API calls, structured/JSON output, temperature, retry+backoff,
       why deterministic stages use temperature=0.
"""
from __future__ import annotations
import json
import time
from typing import Type, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# Two tiers only. Cheap/fast for high-volume parsing; quality for resume prose.
MODELS = {
    "cheap":   "REPLACE_WITH_SMALL_MODEL_ID",
    "quality": "REPLACE_WITH_LARGE_MODEL_ID",
}


def call_llm(system: str, user: str, tier: str = "cheap",
             temperature: float = 0.0) -> str:
    """Raw text call. TODO: implement with your provider's SDK."""
    raise NotImplementedError("Wire up your LLM provider here")


def call_structured(system: str, user: str, schema: Type[T],
                    tier: str = "cheap", retries: int = 2) -> T:
    """Call the model, parse JSON into `schema`, retry on parse/validation failure.

    This is the pattern that makes LLM output safe to store.
    """
    for attempt in range(retries + 1):
        raw = call_llm(system, user, tier=tier, temperature=0.0)
        try:
            return schema.model_validate_json(_extract_json(raw))
        except Exception:
            if attempt == retries:
                raise
            time.sleep(1.5 ** attempt)   # exponential backoff


def _extract_json(text: str) -> str:
    """Strip markdown fences / grab the first {...} block. TODO: harden."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    return text
