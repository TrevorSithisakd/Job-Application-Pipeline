# Job Application Pipeline

LLM pipeline: ingest roles from email -> extract structured data -> score fit
-> draft a tailored resume (RAG + human approval) -> track in a dashboard.

## Run (once stages are implemented)
```
pip install -r requirements.txt
python pipeline.py           # ingest -> extract -> score -> store
streamlit run dashboard.py   # view the board
```

## Flow
```
Gmail alerts -> ingest -> extract(LLM) -> SQLite(jobs)
                                   |
                          fit_score(LLM) -> SQLite(fit)
                                   |
        fact_bank + resume -> tailor(LLM) -> [approval] -> SQLite(resumes)
                                   |
                            Streamlit dashboard
```

## Files
| File | Role |
|------|------|
| `schemas.py` | Pydantic contracts (validation) |
| `llm.py` | One inference wrapper: model tiers, retries, JSON repair |
| `db.py` | SQLite: jobs / applications / resumes |
| `stages/ingest.py` | Gmail -> raw emails |
| `stages/extract.py` | email -> Job |
| `stages/fitscore.py` | Job + profile -> FitScore |
| `stages/tailor.py` | Job + fact bank -> resume draft |
| `pipeline.py` | Orchestrator (run one command) |
| `dashboard.py` | Streamlit board |
| `data/profile.md` | Fit-score context (short) |
| `data/fact_bank.md` | RAG source (truthful facts only) |

## Build order (Week 7 skeleton = get one thread green end to end)
1. `data/profile.md` + `data/fact_bank.md` — fill from your Optiver resume.
2. `llm.py` — wire your provider, get `call_structured` returning a valid object.
3. `stages/extract.py` — paste one job email in, get a valid `Job` out.
4. `db.py` — `init` + `upsert_job` + `all_jobs`.
5. `stages/fitscore.py` — score that one Job.
6. `dashboard.py` — show the row.
7. `stages/ingest.py` — replace the hand-pasted email with a real Gmail pull.
8. `stages/tailor.py` — draft stub last.

> Shipped = run once, real roles from your inbox scored and visible, one resume
> draft generated, pushed to GitHub. Not pretty. Green.

---

## Learning map — what to learn per step

### Ingest (Gmail)
- Gmail API OAuth2 flow; `google-api-python-client`, `google-auth-oauthlib`.
- Gmail query syntax: `from:`, `subject:`, `newer_than:`.
- Email/MIME parsing (`email` stdlib); stripping HTML to clean text.

### Extract
- Prompt design for extraction (instructions + schema + input).
- Structured/JSON output; `temperature=0` for deterministic parsing.
- Pydantic v2 validation; parse-or-retry; dedup/idempotency.

### Fit-score
- The split: rubric (instructions) vs profile (context) vs retrieval (none here).
- Rubric/scoring design; constrained outputs; enum `Literal` fields.
- Calibration — does a score mean the same thing across roles?

### Resume tailor (RAG)
- RAG fundamentals: retrieval vs context-stuffing (v1 stuffs).
- Embeddings + cosine similarity + a vector store (Phase 2 only).
- Grounding / fact-checking to kill hallucination; human-in-the-loop.
- Resume templating.

### Store (SQLite)
- SQL basics; schema design; foreign keys.
- `sqlite3` module; idempotent upserts.

### Inference wrapper
- LLM API usage; retries + exponential backoff.
- Cost/latency; model tiering; caching; token budgeting.

### Dashboard (Streamlit)
- Streamlit basics: `st.dataframe`, widgets, `session_state`.
- Reading a DB into a view; later: edit status, approve/reject.

### Orchestration
- Composing stages; where to catch errors; run idempotency; logging.

## The one concept behind the whole thing
Every LLM call = **construct prompt -> call -> validate output into a schema**.
Extract and fit-score inject *fixed* context (email, profile). Only resume
tailoring uses *retrieved* context, and only that stage needs a grounding check.
