"""STAGE 6 — DASHBOARD. Streamlit reads SQLite directly (no API layer in v1).

LEARN: Streamlit basics (st.dataframe, column_config, caching), reading a DB
       into a table, making links clickable via LinkColumn.
"""
import sqlite3

import streamlit as st

from paths import DB_PATH

st.set_page_config(page_title="Job Application Pipeline", layout="wide")


@st.cache_data(ttl=60)
def load_jobs() -> list[dict]:
    """Every job as a named dict, best fit first. Cached so interacting with
    the table doesn't re-query on every rerun."""
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT company, title, fit_score, track, deadline, url FROM jobs "
            "ORDER BY fit_score IS NULL, fit_score DESC"
        ).fetchall()
    return [dict(r) for r in rows]


st.title("Job application pipeline")

jobs = load_jobs()
st.caption(f"{len(jobs)} roles ingested and scored")

st.dataframe(
    jobs,
    hide_index=True,
    width="stretch",
    column_config={
        "company": st.column_config.TextColumn("Company"),
        "title": st.column_config.TextColumn("Title"),
        "fit_score": st.column_config.ProgressColumn(
            "Fit", min_value=0, max_value=100, format="%d"
        ),
        "track": st.column_config.TextColumn("Track"),
        "deadline": st.column_config.TextColumn("Deadline"),
        # LinkColumn makes the cell an anchor. display_text gives every row the
        # same friendly label instead of showing the raw (often long) URL.
        "url": st.column_config.LinkColumn("Job link", display_text="Open ↗"),
    },
)
# TODO Phase 2: status board, deadline view, resume-version-per-app, approve buttons.
