"""STAGE 6 — DASHBOARD. Streamlit reads SQLite directly (no API layer in v1).

LEARN: Streamlit basics (st.dataframe, st.selectbox, session_state), reading
       a DB into a table, later: approve/reject buttons + status edits.
"""
import sqlite3
import streamlit as st
from paths import DB_PATH

st.title("Job Application Pipeline")

with sqlite3.connect(DB_PATH) as c:
    rows = c.execute(
        "SELECT company, title, fit_score, track, deadline, url FROM jobs "
        "ORDER BY fit_score DESC"
    ).fetchall()

st.dataframe(
    rows,
    column_config=None,   # TODO: name columns, make url clickable
)
# TODO Phase 2: status board, deadline view, resume-version-per-app, approve buttons.
