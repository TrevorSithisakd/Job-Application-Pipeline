"""ONE inference wrapper. Every stage calls through here.

Provider: DeepSeek (OpenAI-compatible API). Swap providers by changing the
client base_url + MODELS below; nothing in the stages changes.

Setup:
    1. Get a key: https://platform.deepseek.com/api_keys
    2. Copy .env.example to .env and put the key in it (a real env var also works)
    3. python llm.py   # runs the smoke test at the bottom

LEARN: LLM API calls, structured/JSON output, temperature, retry+backoff,
       why deterministic stages use temperature=0.
"""
from __future__ import annotations
import os
import time
from typing import Type, TypeVar
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel
from paths import ENV_FILE

T = TypeVar("T", bound=BaseModel)

# Load .env from the project root, not the cwd, so stages work when run from
# anywhere. A real environment variable always wins over the file.
load_dotenv(ENV_FILE)

_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
if not _API_KEY:
    raise RuntimeError(
        "Set DEEPSEEK_API_KEY in .env (copy .env.example). "
        "Get one at https://platform.deepseek.com/api_keys"
    )

# DeepSeek speaks the OpenAI wire format, so we reuse the OpenAI client.
_client = OpenAI(api_key=_API_KEY, base_url="https://api.deepseek.com")

# Two tiers. Flash for high-volume parsing; Pro for resume prose.
MODELS = {
    "cheap":   "deepseek-v4-flash",
    "quality": "deepseek-v4-pro",
}


def call_llm(system: str, user: str, tier: str = "cheap",
             temperature: float = 0.0, json_mode: bool = False) -> str:
    """Raw text call to DeepSeek."""
    if json_mode and "json" not in f"{system}{user}".lower():
        # DeepSeek hard-rejects json_object mode unless the prompt says "json".
        # A stage that forgets fails 400 on EVERY call, so guarantee it here
        # rather than trusting each prompt to remember.
        system = f"{system}\n\nRespond with a single valid JSON object."
    kwargs: dict = dict(
        model=MODELS[tier],
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        # Non-thinking mode: faster, cheaper, deterministic — right for parsing.
        # If this ever 400s, delete the extra_body line (thinking defaults on).
        extra_body={"thinking": {"type": "disabled"}},
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = _client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content


def call_structured(system: str, user: str, schema: Type[T],
                    tier: str = "cheap", retries: int = 2,
                    temperature: float = 0.0) -> T:
    """Call, parse JSON into `schema` (pydantic model), retry on failure.

    json_mode=True asks DeepSeek to emit valid JSON. Your prompt must mention
    JSON for this to engage — the stage system prompts already do.

    temperature defaults to 0 (deterministic parsing). The resume tailor passes
    a little warmth for prose; structure still holds because json_mode + this
    validation + retry catch malformed output regardless of sampling.
    """
    for attempt in range(retries + 1):
        raw = call_llm(system, user, tier=tier, temperature=temperature, json_mode=True)
        try:
            return schema.model_validate_json(_extract_json(raw))
        except Exception:
            if attempt == retries:
                raise
            time.sleep(1.5 ** attempt)   # exponential backoff


def _extract_json(text: str) -> str:
    """Strip markdown fences and grab the outermost {...} block."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return text


if __name__ == "__main__":
    # Smoke test — proves the key + wiring work before you touch any stage.
    print("cheap tier says:", call_llm("You are terse.", "Reply with the word OK."))
