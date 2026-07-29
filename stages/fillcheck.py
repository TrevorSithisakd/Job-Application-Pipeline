"""One-page fitting for rendered resumes (from "(C) HANDOFF-resume-docx-format.md").

Two jobs:
  1. MEASURE how tall a resume is — how many pages, how full page 1 is.
  2. FIT a draft to exactly one page by tightening spacing/font, then trimming.

Measurement has three backends, tried in this order of accuracy:
  - `estimate`  : pure Python, ZERO external deps. Predicts height from the same
                  spec constants render.py uses. Approximate (±a few %) but instant
                  and portable — this is what the fit loop uses each iteration.
  - `word`      : docx2pdf drives an installed MS Word -> PDF (exact).
  - `libreoffice`: `soffice --headless --convert-to pdf` (exact, cross-platform).

So the fit loop runs anywhere on the estimator; Word/LibreOffice, if present, only
upgrade the FINAL check from "estimated one page" to "verified one page".

Run:  python -m stages.fillcheck <resume.docx>     # accurate check on a file
"""
from __future__ import annotations
import io
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from schemas import ResumeDraft
from stages import render as R
from stages.render import Style

TARGET_LOW, TARGET_HIGH = 93.0, 96.0
PT_PER_IN = 72.0

# --- Estimator calibration (tuned against a Word-rendered reference) ---------
LINE_BOX = 1.15    # a Calibri line box is ~1.15 x the font size
CHAR_W = 0.52      # average glyph advance ~= CHAR_W x font_pt
CAL = 0.87         # global height correction: estimate ran ~13% tall vs Word


def _usable() -> tuple[float, float]:
    h = R.PAGE_H_PT - (R.MARGIN["top"] + R.MARGIN["bottom"]) * PT_PER_IN
    w = R.PAGE_W_PT - (R.MARGIN["left"] + R.MARGIN["right"]) * PT_PER_IN
    return h, w


def _lines(text: str, font_pt: float, avail_w: float) -> int:
    if not text:
        return 1
    return max(1, math.ceil(len(text) * CHAR_W * font_pt / avail_w))


def _ph(text, font_pt, before, after, avail_w, line=1.0) -> float:
    """Estimated height of one paragraph in points."""
    return _lines(text, font_pt, avail_w) * font_pt * LINE_BOX * line + before + after


def estimate_fill(draft: ResumeDraft, style: Style | None = None) -> dict:
    """Predict {'pages', 'fill_pct'} from the draft + style, no rendering.
    fill_pct = 100 * content_height / usable_page_height (can exceed 100 = overflow)."""
    st = style or Style()
    H, W = _usable()
    fs, sp = st.fs, st.sp
    bw = W - R.BULLET_INDENT_IN * PT_PER_IN - 12   # bullet indent + marker glyph

    total = _ph(R.NAME, fs(R.NAME_PT), 0, sp(R.NAME_AFTER), W)
    if draft.tagline:
        total += _ph(draft.tagline, fs(R.TAGLINE_PT), 0, sp(R.TAGLINE_AFTER), W)
    total += _ph(R.CONTACT, fs(R.CONTACT_PT), 0, sp(R.CONTACT_AFTER), W)

    def heading():
        return _ph("X", fs(R.HEADING_PT), sp(R.HEADING_BEFORE), sp(R.HEADING_AFTER), W)

    total += heading() + _ph(draft.profile, fs(R.BODY_PT), 0, sp(R.BODY_AFTER), W)
    total += heading() + _ph(draft.education, fs(R.BODY_PT), 0, sp(1), W)

    total += heading()
    for p in draft.projects:
        total += _ph(p.title + (p.angle or ""), fs(R.ROLE_PT), sp(R.ROLE_BEFORE), sp(R.ROLE_AFTER), W)
        sub = "  ·  ".join(x for x in (p.stack, p.url) if x)
        if sub:
            total += _ph(sub, fs(R.SUB_PT), 0, sp(R.SUB_AFTER), W)
        for b in p.bullets:
            total += _ph(b, fs(R.BULLET_PT), 0, sp(R.BULLET_AFTER), bw, line=R.BULLET_LINE)
    if draft.additional:
        total += _ph(draft.additional, fs(R.BODY_PT), 0, sp(1), W)

    total += heading()
    for s in draft.skills:
        total += _ph(f"{s.label}: {s.content}", fs(R.SKILL_PT), 0, sp(R.SKILL_AFTER), W)

    if draft.experience:
        total += heading()
        for e in draft.experience:
            total += _ph(e.role + (e.org or ""), fs(R.ROLE_PT), sp(R.ROLE_BEFORE), sp(R.ROLE_AFTER), W)
            for b in e.bullets:
                total += _ph(b, fs(R.BULLET_PT), 0, sp(R.BULLET_AFTER), bw, line=R.BULLET_LINE)

    total *= CAL   # correct to the Word reference
    pages = max(1, math.ceil(total / H - 1e-6))
    return {"pages": pages, "fill_pct": round(100 * total / H, 1), "backend": "estimate"}


# --- Accurate backends (optional) -------------------------------------------

def _find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    win = r"C:\Program Files\LibreOffice\program\soffice.exe"
    return win if Path(win).exists() else None


def accurate_backend() -> str | None:
    """Which exact backend is available, if any."""
    try:
        import docx2pdf  # noqa: F401
        return "word"
    except Exception:
        pass
    return "libreoffice" if _find_soffice() else None


def _convert_to_pdf(docx_path: Path, pdf_path: Path) -> str:
    try:
        from docx2pdf import convert
        convert(str(docx_path), str(pdf_path))
        if pdf_path.exists():
            return "word"
    except Exception:
        pass
    soffice = _find_soffice()
    if soffice:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir",
                        str(pdf_path.parent), str(docx_path)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        produced = pdf_path.parent / (docx_path.stem + ".pdf")
        if produced.exists():
            if produced != pdf_path:
                produced.replace(pdf_path)
            return "libreoffice"
    raise RuntimeError("No docx->PDF converter: install MS Word + `docx2pdf`, or LibreOffice.")


def check_fill(docx_path, dpi: int = 150) -> dict:
    """EXACT {'pages', 'fill_pct'} by rendering the .docx to PDF. Needs Word or
    LibreOffice + pymupdf. fill_pct = how far down page 1 the content reaches."""
    try:
        import fitz  # PyMuPDF
        import numpy as np
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("check_fill needs pymupdf + Pillow + numpy.") from e
    docx_path = Path(docx_path)
    with tempfile.TemporaryDirectory() as td:
        pdf_path = Path(td) / (docx_path.stem + ".pdf")
        backend = _convert_to_pdf(docx_path, pdf_path)
        with fitz.open(str(pdf_path)) as pdf:
            pages = pdf.page_count
            pix = pdf.load_page(0).get_pixmap(dpi=dpi)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
        a = np.asarray(img)
        inked = np.where((a < 245).any(axis=1))[0]
        fill = round(100 * (int(inked.max()) + 1) / a.shape[0], 1) if inked.size else 0.0
    return {"pages": pages, "fill_pct": fill, "backend": backend}


# --- The fit loop ------------------------------------------------------------
# Non-destructive first (tighten spacing, then font), content trimming last.
_LADDER = [
    Style(1.0, 1.0), Style(1.0, 0.8), Style(1.0, 0.6),
    Style(0.98, 0.6), Style(0.96, 0.5), Style(0.94, 0.45), Style(0.92, 0.4),
]


def fit_to_one_page(draft: ResumeDraft) -> tuple[ResumeDraft, Style, dict, list[str]]:
    """Return (draft_to_render, style, estimate, notes) that fits one page.
    Uses the estimator each step. Scales spacing/font first; trims content only
    if scaling alone can't fit."""
    for st in _LADDER:
        est = estimate_fill(draft, st)
        if est["pages"] <= 1:
            return draft, st, est, []

    st = _LADDER[-1]
    d = draft.model_copy(deep=True)
    notes: list[str] = []
    fits = lambda: estimate_fill(d, st)["pages"] <= 1

    if d.additional:
        d.additional = None
        notes.append("dropped the additional line")
        if fits():
            return d, st, estimate_fill(d, st), notes
    if any(len(p.bullets) > 2 for p in d.projects):
        for p in d.projects:
            p.bullets = p.bullets[:2]
        notes.append("capped project bullets to 2")
        if fits():
            return d, st, estimate_fill(d, st), notes
    while len(d.projects) > 3 and not fits():
        dropped = d.projects.pop()
        notes.append(f"dropped project: {dropped.title}")
    if fits():
        return d, st, estimate_fill(d, st), notes
    for p in d.projects:
        p.bullets = p.bullets[:1]
    for e in d.experience:
        e.bullets = e.bullets[:1]
    notes.append("capped all bullets to 1")
    return d, st, estimate_fill(d, st), notes


def verdict(result: dict) -> str:
    pages, fill = result["pages"], result["fill_pct"]
    tag = result.get("backend", "?")
    if pages > 1:
        return f"OVERFLOW: {pages} pages ({fill}%, {tag}). Trim content or spacing."
    if fill < TARGET_LOW:
        return f"SPARSE: {fill}% filled ({tag}). Add a bullet or widen spacing."
    if fill > TARGET_HIGH:
        return f"TIGHT: {fill}% filled ({tag}) — fits, little slack."
    return f"OK: 1 page, {fill}% filled ({tag}); target {TARGET_LOW}-{TARGET_HIGH}."


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m stages.fillcheck <resume.docx>")
    res = check_fill(sys.argv[1])
    print(res)
    print(verdict(res))
