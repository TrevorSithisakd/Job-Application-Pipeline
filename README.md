# Job Application Pipeline

A local, single-user app that turns your job-alert inbox into a tailored-resume
production line:

**Gmail alerts → extract every posting → score fit → draft a tailored resume →
fact-check it → you approve → download a `.docx`.**

Everything runs on your machine. Your inbox, database, resumes, and API keys
never leave it — none of that is in this repository (see [Privacy](#privacy)).

---

## What it does

- **Ingest** job-alert emails from Gmail (LinkedIn / SEEK / Indeed / Greenhouse).
- **Extract** *every* posting from an email — alert digests list many jobs — into
  validated records.
- **Fit-score** each role against your profile (0–100 + rationale + missing
  keywords + a track: ml-engineer / data-scientist / data-analyst).
- **Tailor** a resume for a role using only facts from your fact bank (RAG-style),
  then **grounding-check** every claim against that fact bank — an unsupported
  claim (e.g. "fine-tuned" when you only "used" a model) is flagged and blocks
  approval.
- **Render** the approved draft to a `.docx`.
- **Track** it all in a local web app: a status board (drag between
  interested → applied → interviewing → offer → rejected), a table, a job detail
  view, manual role entry, and a "run ingest" button.

## How it works

```
Gmail alerts ─▶ ingest ─▶ extract (LLM, one email → many Jobs) ─▶ SQLite(jobs)
                                                │
                                       fit-score (LLM) ─▶ SQLite(fit + track)
                                                │
        fact bank + JD ─▶ tailor (LLM) ─▶ grounding check (LLM) ─▶ SQLite(resumes) + .docx
                                                │
                                    FastAPI + web UI (localhost)
```

Two ideas hold it together:
1. **Every stage boundary is a typed contract** (a Pydantic schema). If an LLM's
   output doesn't validate, it's rejected and retried.
2. **The probabilistic surface is kept small.** LLMs draft and fact-check;
   plain Python renders the `.docx` and enforces structure — so the output is
   reproducible and testable.

## Requirements

- **Python 3.10+**
- A **DeepSeek API key** (the default LLM provider — OpenAI-compatible, cheap).
  Swappable in `llm.py`.
- A **Google Cloud project** with the Gmail API enabled, for reading your alerts.

## Setup

```bash
git clone <your-fork-url>
cd job-application-pipeline

python -m venv job_pipe_env
# Windows:  job_pipe_env\Scripts\activate
# macOS/Linux:  source job_pipe_env/bin/activate
pip install -r requirements.txt
```

### 1. LLM key
```bash
cp .env.example .env        # then edit .env and paste your key
# DEEPSEEK_API_KEY=sk-...
```

### 2. Your profile and fact bank (required)
The app fails loudly without these — by design, so it never scores or drafts
against nothing.
```bash
cp data/profile.example.md    data/profile.md
cp data/fact_bank.example.md  data/fact_bank.md
```
Fill both with your **real, verifiable** details. The fact bank is the *only*
source the resume writer may use, and the grounding check rejects anything not in
it — so keep it truthful and specific.

### 3. Gmail access
1. In the [Google Cloud Console](https://console.cloud.google.com/): create a
   project, enable the **Gmail API**, and create an **OAuth client ID** of type
   **Desktop app**.
2. Download it as **`credentials.json`** into the project root.
3. The first run opens a browser to grant **read-only** Gmail access and writes
   `token.json`. (Tokens for an app in "testing" expire after ~7 days — set the
   OAuth consent screen to "In production" to stop the weekly re-auth.)

## Usage

### Run the pipeline (fetch + score)
```bash
python -m pipeline        # last 7 days
python -m pipeline 3      # last 3 days
```
Ingests your alerts, extracts all postings, and fit-scores any **new** ones
(already-scored jobs are skipped, so re-runs are fast and cheap).

### Run the web app
```bash
python run_app.py         # or double-click run_app.bat (Windows)
```
Opens `http://127.0.0.1:8000`. From there you can:
- browse the **board / table**, filter, and drag jobs between statuses;
- open a role, **paste the full JD** (alerts only carry a teaser), then **Tailor**;
- review the resume **preview** and the **grounding** report, **Approve**, and
  **Download .docx**;
- **Add a role manually**, **delete** a role, or **run ingest** for a chosen
  window right from the toolbar.

### Tests
```bash
python -m pytest -q       # offline; LLM calls are mocked
```

## Project structure

| Path | Role |
|------|------|
| `schemas.py` | Pydantic contracts for every LLM output |
| `llm.py` | One inference wrapper: model tiers, retries, JSON repair |
| `db.py` | SQLite: jobs / resumes, dedup, migrations |
| `stages/ingest.py` | Gmail → cleaned email bodies |
| `stages/extract.py` | email → list of `Job`s (digests → many) |
| `stages/fitscore.py` | Job + profile → fit score |
| `stages/tailor.py` | Job + fact bank → draft + grounding check |
| `stages/render.py` | `ResumeDraft` → `.docx` (deterministic) |
| `pipeline.py` | Orchestrator — one command |
| `api.py` | FastAPI: JSON API + serves the frontend |
| `frontend/` | The web UI (no build step) |
| `run_app.py` / `run_app.bat` | One-click launcher |
| `data/*.example.md` | Templates for your profile + fact bank |

## Privacy

Nothing personal is tracked by git — not now and not in history. `.gitignore`
excludes `applications.db` (+ backups), `token.json`, `credentials.json`, `.env`,
your `data/profile.md` and `data/fact_bank.md`, and `data/resumes/`. A clone
contains only code and the example templates.

## Notes & limits

- **Local, single-user.** No auth, meant to run on your own machine at localhost.
- **Alert emails are teasers.** The full JD lives behind each posting's link;
  paste it into the job before tailoring (scraping is deliberately avoided to
  respect the job boards' terms).
- **Dedup** is by normalized `company + title`, since the same role appears across
  boards with different tracking URLs.
- Tailoring makes two quality-tier LLM calls per resume; fit-scoring makes one
  cheap call per new job.
