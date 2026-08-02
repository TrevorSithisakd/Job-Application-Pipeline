"""Boot helper for a fresh (demo/cloud) deploy.

A clone has no personal data (profile.md / fact_bank.md are gitignored) and an
empty DB, so the app would crash on startup. This makes a bare deploy come up
working — and is a NO-OP on your real local setup (real files + data already
present), so it never overwrites anything.

Two jobs, both idempotent:
  ensure_files() — make data/profile.md + data/fact_bank.md exist. Priority:
      1. env vars PROFILE_MD / FACT_BANK_MD (paste your real content in the host's
         dashboard for an authentic demo — kept out of the public repo);
      2. else copy the committed *.example.md templates (a demo persona).
  ensure_seed()  — if the jobs table is empty, insert a few pre-scored sample jobs
      so the board isn't blank. Skipped the moment any real job exists.

IMPORTANT: this module must not import the stage modules (fitscore/tailor read the
data files at import time). Call ensure_files() BEFORE importing those.
"""
from __future__ import annotations
import os

import db
from paths import ROOT, PROFILE_FILE, FACT_BANK_FILE
from schemas import Job, FitScore

_DATA = ROOT / "data"


def ensure_files() -> None:
    for env, path, example in (
        ("PROFILE_MD", PROFILE_FILE, _DATA / "profile.example.md"),
        ("FACT_BANK_MD", FACT_BANK_FILE, _DATA / "fact_bank.example.md"),
    ):
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        content = os.environ.get(env)
        if content:
            path.write_text(content, encoding="utf-8")
        elif example.exists():
            path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")


# (company, title, track, location, salary, status, score, rationale, jd)
_DEMO = [
    ("Atlassian", "Machine Learning Engineer, Graduate Program", "ml-engineer",
     "Sydney, hybrid", "$110k-125k", "interviewing", 86,
     "Strong match: graduate ML engineering on production data maps onto the "
     "forecasting and LLM-pipeline projects; the main gap is production serving.",
     "Join our ML Platform group building models behind search, recommendations "
     "and issue triage across Jira and Confluence. You'll own models end to end: "
     "features, training, evaluation, and deployment. Python, SQL, one deep-learning "
     "framework; experiment design and rigorous offline evaluation expected."),
    ("Canva", "Data Scientist, Growth", "data-scientist",
     "Sydney, hybrid", "$120k-140k", "applied", 82,
     "Good fit on statistics and experimentation (A/B testing, causal inference); "
     "weaker on a commercial product-analytics stack.",
     "Growth Data Science sits with product and marketing, measuring what moves "
     "activation and retention for 200M+ users. Heavy on experimentation, causal "
     "inference, SQL, and clear communication of uncertainty to stakeholders."),
    ("Commonwealth Bank", "Data Scientist, Retail Analytics", "data-scientist",
     "Eveleigh NSW", "$105k-118k", "interested", 76,
     "Solid but not distinctive: reliable delivery on tabular data with strong SQL; "
     "lead with pipelines and validation over deep learning.",
     "Retail Analytics builds propensity and churn models across 15M customer "
     "relationships. Strong SQL, tabular ML, and dependable delivery on regulated "
     "data. Experience with model monitoring is a plus."),
    ("Quantium", "Machine Learning Engineer", "ml-engineer",
     "Sydney, hybrid", "$115k-130k", "interested", 80,
     "Retail data science at scale, heavy on pipelines and reproducibility; the "
     "MLflow and walk-forward validation work reads directly onto it. No Spark.",
     "Turn transaction data from major retailers into products used by hundreds of "
     "brands. Build reproducible pipelines at scale. Python, SQL, and a modern "
     "experiment-tracking stack; Spark/Databricks a plus."),
    ("Woolworths Group", "Data Scientist", "data-scientist",
     "Bella Vista NSW", "$110k-128k", "applied", 74,
     "Applied forecasting and demand modelling on tabular retail data; a clean fit "
     "for the forecasting pipeline, lighter on the deep-learning side.",
     "Forecasting and optimisation across supply chain and retail. Build and "
     "validate demand models, work with engineering to productionise them. Python, "
     "SQL, time-series and evaluation discipline."),
    ("Macquarie Group", "Data Analyst, Banking & Financial Services", "data-analyst",
     "Martin Place, Sydney", "$95k-105k", "interested", 63,
     "Below level: reporting and dashboards with SQL and Power BI. Tooling fits, "
     "but the ML depth would sit unused.",
     "Support business decisions with reporting, self-service dashboards, and "
     "ad-hoc analysis. Strong SQL and a BI tool (Power BI / Tableau). Clear "
     "communication with non-technical stakeholders."),
]


def ensure_seed() -> None:
    db.init()
    if db.all_jobs():                      # real data present -> never seed
        return
    for company, title, track, loc, salary, status, score, rationale, jd in _DEMO:
        job = Job(source="demo", company=company, title=title, jd_text=jd,
                  location=loc, salary=salary)
        job_id = db.upsert_job("demo", job)
        db.set_fit(job_id, FitScore(score=score, rationale=rationale,
                                    missing_keywords=[], track=track))
        db.set_status(job_id, status)
