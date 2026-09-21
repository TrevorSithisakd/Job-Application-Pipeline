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
- **Fit-score** each role against your profile and your stated preferences
  (0–100 + rationale + missing keywords + a track). The weights, score bands, and
  track names are yours to set in Setup.
- **Tailor** a resume for a role using only facts from your fact bank (RAG-style),
  then **grounding-check** every claim against that fact bank — an unsupported
  claim (e.g. "fine-tuned" when you only "used" a model) is flagged and blocks
  approval.
- **Fit & render** the draft to a **one-page** `.docx` in a fixed house style
  (Calibri, navy headings), auto-tightened to fill exactly one page.
- **Track** it all in a local web app: a status board (drag between
  interested → applied → interviewing → offer → rejected), a table, a job detail
  view, manual role entry, uploading an existing resume, quick job delete, and a
  "run ingest" button.
- **Configure** all of it from a **Setup** page in the browser — API key, your
  profile and fact bank (buildable from a resume you already have), what an ideal
  role looks like, how fit is scored, and which senders to scrape. No file
  editing, no restart.

## How it works

```
Gmail alerts ─▶ ingest ─▶ extract (LLM, one email → many Jobs) ─▶ SQLite(jobs)
                                                │
                                       fit-score (LLM) ─▶ SQLite(fit + track)
                                                │
   fact bank + JD ─▶ tailor (LLM) ─▶ grounding (LLM) ─▶ one-page fit ─▶ render ─▶ SQLite(resumes) + .docx
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

python run_app.py           # or double-click givemeajob.bat (Windows)
```

The app starts **without any configuration** and opens on a board with a
**Finish setup** banner. Everything below is done in the browser, under **Setup**
in the top nav — nothing needs a file editor or a restart.

### 1. API key
Paste your [DeepSeek key](https://platform.deepseek.com/api_keys) into the
**API key** pane and press **Test key** to confirm it works before spending a
real run finding out it doesn't. It's written to `.env`, which is gitignored. A
real `DEEPSEEK_API_KEY` environment variable still takes priority.

### 2. Your profile and fact bank (required)
Under **Profile & fact bank**:
- **Profile** — a short summary of you. This is what fit scoring judges roles against.
- **Fact bank** — the *only* source the resume writer may draw on. **Upload a
  `.docx`/`.pdf` resume** and it's turned into a fact bank for you; it's shown in
  the editor **for review** and saved only when you press Save. Anything the
  resume was vague about comes back marked `(verify)`.

Both start as a demo persona, and the pane says so until you replace them — a fit
score against someone else's CV looks completely normal while meaning nothing.
Keep the fact bank truthful and specific: the grounding check rejects any resume
claim that isn't in it.

*(Prefer the terminal? `cp data/profile.example.md data/profile.md` and
`cp data/fact_bank.example.md data/fact_bank.md` still work — the Setup pane
reads and writes those same two files.)*

### 3. What an ideal job looks like, and how fit is judged
- **Ideal job** — target titles, locations, remote policy, seniority, salary
  floor, must-haves, nice-to-haves, dealbreakers, and your own track names.
- **Fit criteria** — a weight per scoring dimension, the score bands (what 80+
  *means*), and a free-text box for anything else. **Preview the prompt** shows
  the exact text your sliders generate, and **Rescore the board** re-applies
  changed criteria to roles already scored.

### 4. Which emails to scrape
Under **Email sources**, **Scan inbox** sweeps your Updates tab and lists every
sender it finds, with the job boards already ticked and a sample subject line
under each as evidence. Tick what you want, add anything it missed by hand, and
Save. Until you do, ingest falls back to the built-in job-board list, so it
behaves exactly as it did before.

The scan needs Gmail authorised first (step 5).

### 5. Gmail access
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
python run_app.py         # or double-click givemeajob.bat (Windows)
```
Opens `http://127.0.0.1:8000`. From there you can:
- browse the **board / table**, filter, and drag jobs between statuses;
- open a role, **paste the full JD** (alerts only carry a teaser), then **Tailor**;
- review the resume **preview** and the **grounding** report, **Approve**, and
  **Download .docx**;
- **Upload** an existing `.docx`/`.pdf` resume to a role (kept as an "uploaded" version);
- **Add a role manually**, **quick-delete** a role (× on any card/row), or
  **run ingest** for a chosen window right from the toolbar;
- open **Setup** to change your key, profile, fact bank, ideal-job preferences,
  scoring criteria, or email sources — all without restarting.

### Tests
```bash
pip install -r requirements-dev.txt
python -m pytest -q       # offline; LLM calls are mocked
```

## Project structure

| Path | Role |
|------|------|
| `schemas.py` | Pydantic contracts for every LLM output |
| `llm.py` | One inference wrapper: model tiers, retries, JSON repair |
| `db.py` | SQLite: jobs / resumes, dedup, migrations |
| `stages/ingest.py` | Gmail → cleaned email bodies; inbox sender discovery |
| `stages/extract.py` | email → list of `Job`s (digests → many) |
| `stages/fitscore.py` | Job + profile → fit score; builds the rubric from settings |
| `stages/tailor.py` | Job + fact bank → draft + grounding check + one-page fit |
| `stages/render.py` | `ResumeDraft` → styled one-page `.docx` (deterministic) |
| `stages/fillcheck.py` | One-page fit: estimate height, tighten/trim to one page |
| `stages/factbank.py` | Uploaded resume → fact-bank markdown (for your review) |
| `settings.py` | Runtime config: API key (`.env`) + `data/settings.json` |
| `pipeline.py` | Orchestrator — one command |
| `api.py` | FastAPI: JSON API + serves the frontend |
| `frontend/` | The web UI, incl. the Setup pane (no build step) |
| `run_app.py` / `givemeajob.bat` | One-click launcher |
| `seed.py` | Boot helper: copies the example profile/fact bank in on a fresh install |
| `data/*.example.md` | Templates for your profile + fact bank |

## Privacy

Nothing personal is tracked by git — not now and not in history. `.gitignore`
excludes `applications.db` (+ backups), `token.json`, `credentials.json`, `.env`,
your `data/profile.md` and `data/fact_bank.md`, and `data/resumes/`. A clone
contains only code and the example templates.

## Notes & limits

- **Local, single-user.** No auth, meant to run on your own machine at localhost.
- **Sender discovery** is in Setup → Email sources (it used to mean running
  `python -m stages.ingest audit` and reading the output by eye — that still
  works, and now prints a `JOB?` marker beside the likely ones).
- **Alert emails are teasers.** The full JD lives behind each posting's link;
  paste it into the job before tailoring (scraping is deliberately avoided to
  respect the job boards' terms).
- **Dedup** is by normalized `company + title`, since the same role appears across
  boards with different tracking URLs.
- **One-page fit** uses a dependency-free height estimator, so it runs anywhere.
  An *exact* page check (`python -m stages.fillcheck <docx>`) is optional and needs
  MS Word (`docx2pdf`) or LibreOffice installed.
- Tailoring makes two quality-tier LLM calls per resume; fit-scoring makes one
  cheap call per new job.
