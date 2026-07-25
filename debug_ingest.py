"""DIAGNOSTIC — dump ONE job-alert email BEFORE and AFTER body-stripping.

Purpose: find where the job description is lost. It compares the raw MIME parts
(what Gmail actually sent) against what `ingest.extract_text` hands downstream,
so you can see whether the JD is (a) stripped away by our cleaning, or (b) never
in the email to begin with (a teaser behind a "view job" link).

Read-only Gmail access. Run from the project root:
    python debug_ingest.py                 # first email from a 30-day query
    python debug_ingest.py <gmail_msg_id>  # a specific message

Full before/after text is written to debug_out/ (gitignored) so nothing is
truncated; a summary + preview prints to the console.
"""
from __future__ import annotations
import sys

from stages import ingest
from paths import ROOT

OUT = ROOT / "debug_out"
QUERY_30D = (
    'from:(jobalerts-noreply@linkedin.com OR jobs-noreply@linkedin.com '
    'OR jobs-listings@linkedin.com OR notifications@us.greenhouse-jobs.com '
    'OR seek.com.au OR indeed.com OR jobs2web.com) newer_than:30d'
)


def _headers(payload) -> dict:
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


def main() -> None:
    service = ingest._gmail_service()

    if len(sys.argv) > 1:
        msg_id = sys.argv[1]
    else:
        resp = service.users().messages().list(userId="me", q=QUERY_30D).execute()
        msgs = resp.get("messages", [])
        if not msgs:
            print("No emails matched the query."); return
        msg_id = msgs[0]["id"]

    full = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    payload = full["payload"]
    hdrs = _headers(payload)

    print(f"message id : {msg_id}")
    print(f"from       : {hdrs.get('from', '?')}")
    print(f"subject    : {hdrs.get('subject', '?')}")
    print(f"snippet    : {full.get('snippet', '')[:140]}")
    print("-" * 64)

    # BEFORE any of our cleaning: the raw decoded parts.
    raw_plain = ingest._find_part(payload, "text/plain")
    raw_html = ingest._find_part(payload, "text/html")
    # AFTER our full pipeline: exactly what goes to the extract stage.
    after = ingest.extract_text(payload)

    OUT.mkdir(exist_ok=True)

    def dump(name: str, content: str | None) -> int:
        (OUT / name).write_text(content or "", encoding="utf-8")
        return len(content) if content else 0

    n_plain = dump("1_raw_plain.txt", raw_plain)
    n_html = dump("2_raw_html.txt", raw_html)
    n_after = dump("3_after_strip.txt", after)

    print(f"raw text/plain : {n_plain:>6} chars  -> debug_out/1_raw_plain.txt")
    print(f"raw text/html  : {n_html:>6} chars  -> debug_out/2_raw_html.txt")
    print(f"AFTER strip    : {n_after:>6} chars  -> debug_out/3_after_strip.txt")
    print("-" * 64)

    # Reproduce what the cleaner sees, to attribute the loss precisely.
    if raw_plain:
        source, kind = raw_plain, "text/plain"
    elif raw_html:
        source, kind = ingest._strip_html(raw_html), "text/html (after _strip_html)"
    else:
        source, kind = "", "none"
    print(f"cleaner input  : {kind}, {len(source)} chars")

    marker = "This email was intended"
    if marker in source:
        kept = source.split(marker)[0]
        print(f"!! '{marker}' FOUND — _clean_text truncates here.")
        print(f"   kept before marker : {len(kept)} chars")
        print(f"   DROPPED after it    : {len(source) - len(kept)} chars")
    else:
        print(f"   '{marker}' not present (no truncation from that split).")

    # Final link in the chain: how much of the cleaned body the extract LLM keeps
    # as jd_text. If AFTER-strip is long but jd_text is short, the LLM is the leak,
    # not the cleaner. (Costs one LLM call; skip with `noextract` arg.)
    if "noextract" not in sys.argv:
        print("-" * 64)
        try:
            from stages import extract
            job = extract.extract(after)
            dump("4_extracted_jd.txt", job.jd_text)
            print(f"extract -> jd_text : {len(job.jd_text):>6} chars  "
                  f"(company={job.company!r}, title={job.title!r})")
            print("                     -> debug_out/4_extracted_jd.txt")
            if n_after and len(job.jd_text) < 0.5 * n_after:
                print("!! jd_text is <50% of the cleaned body — the EXTRACT stage is "
                      "dropping description text, not the cleaner.")
        except Exception as e:
            print(f"extract failed (email may not be a posting): {type(e).__name__}: {e}")

    print("\n--- AFTER-STRIP PREVIEW (first 900 chars) ---")
    print((after or "")[:900])


if __name__ == "__main__":
    main()
