"""ONE inference wrapper. Every stage calls through here.

Provider: DeepSeek (OpenAI-compatible API). Swap providers by changing the
client base_url + MODELS below; nothing in the stages changes.

Setup:
    1. Get a key: https://platform.deepseek.com/api_keys
    2. Enter it in the app's Setup pane, or put it in .env yourself
       (a real env var also works and always wins)
    3. python llm.py   # runs the smoke test at the bottom

The key is resolved LAZILY, on the first call — never at import. Reading it at
import made `import llm` raise without a key, which took `api.py` down with it:
the server couldn't boot, so there was no way to reach the page that sets the
key. Deferring it to call time keeps the error just as loud (a stage still fails
immediately and says why) without making the app unbootable.

LEARN: LLM API calls, structured/JSON output, temperature, retry+backoff,
       why deterministic stages use temperature=0, import-time vs call-time
       configuration.
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

NO_KEY_MESSAGE = (
    "No DEEPSEEK_API_KEY set. Add it in the app's Setup pane, or put it in .env "
    "(copy .env.example). Get one at https://platform.deepseek.com/api_keys"
)

BASE_URL = "https://api.deepseek.com"

# Two tiers. Flash for high-volume parsing; Pro for resume prose.
MODELS = {
    "cheap":   "deepseek-v4-flash",
    "quality": "deepseek-v4-pro",
}

_client: OpenAI | None = None   # built on first use, dropped by reset_client()


def _api_key() -> str | None:
    """The key, or None. Loads .env from the project root (not the cwd) so this
    works whatever directory you launched from. load_dotenv does NOT override an
    existing environment variable, so a real env var still wins over the file —
    which is what the Render deploy relies on.

    Re-reading on every call is deliberate: the Setup pane rewrites .env at
    runtime, and the new value has to be visible without a restart.
    """
    load_dotenv(ENV_FILE, override=False)
    key = os.environ.get("DEEPSEEK_API_KEY")
    return key.strip() if key and key.strip() else None


def has_key() -> bool:
    """Cheap check for the UI — is this app configured enough to make a call?"""
    return _api_key() is not None


def reset_client() -> None:
    """Drop the cached client so the next call rebuilds it with the current key.
    Call this after writing a new key, or the old one stays live for the process."""
    global _client
    _client = None


def _get_client() -> OpenAI:
    """The cached client, built on first use. Raises the same clear error the old
    import-time check did — just at the point of use."""
    global _client
    if _client is None:
        key = _api_key()
        if not key:
            raise RuntimeError(NO_KEY_MESSAGE)
        # DeepSeek speaks the OpenAI wire format, so we reuse the OpenAI client.
        _client = OpenAI(api_key=key, base_url=BASE_URL)
    return _client


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
    resp = _get_client().chat.completions.create(**kwargs)
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


def smoke_test() -> str:
    """One cheap round-trip proving the key + wiring work. Exposed as a function
    (not just a __main__ block) so the Setup pane's "Test key" button runs the
    exact same check — better to find a bad key here than three stages deep."""
    return call_llm("You are terse.", "Reply with the word OK.")


if __name__ == "__main__":
    print("cheap tier says:", smoke_test())
