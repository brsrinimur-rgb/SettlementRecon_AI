# SettlementRecon AI (v1.3.0)

Standalone monthly Bank & POS Settlement Reconciliation application.
Bank & POS Settlement Reconciliation • Monthly Finance Control

This is a consolidated rebuild folding together everything from v1.0
through v1.2.4 into one clean release, with deployment gotchas documented
below so they don't need rediscovering.

## Run on Windows

1. Extract the package.
2. Double-click `run_app.bat`.
3. Browser opens automatically.
4. Upload the monthly ANB bank statement.
5. Under "POS Transaction Reports — add in small groups", select 5-10 POS
   files at a time and click "➕ Add batch to month". Repeat until the
   whole month's POS files have been added (check the running total shown).
6. Optionally upload a POS Terminal ID master (a saved one is used by
   default).
7. Click **Run Monthly Reconciliation**.
8. Review Dashboard, Detailed Recon, Date-wise, Store-wise, Date + Store,
   Exceptions, Mapping Review, and Parser Audit.
9. Download the final Excel report.
10. Click "🗑 Start new month" before beginning the next month's cycle.

## Run anywhere (Render, other hosts)

```bash
pip install -r requirements.txt
streamlit run app.py
```

The `.streamlit/config.toml` in this package disables Streamlit's "magic"
auto-display feature — see "Known deployment gotchas" below for why.

## Matching engine

- POS files: reads `Details_mada` and `Details_CC` sheets.
- Bank: parses ANB POS settlement narration for Retailer ID, Terminal ID,
  the embedded original transaction date, scheme (**MADA** / **CC** /
  **GCC** — kept strictly separate; anything unrecognized goes to the
  Parser Audit instead of being guessed), fee, VAT, and transaction count.
- Duplicate POS extracts (from re-pulling the POS portal mid-month) are
  removed via a transaction-level fingerprint.
- Matching: exact same-day Terminal + Retailer + Scheme + Date first, then
  a date-shift pass (default 10 days, configurable 0-15) for legitimate
  settlement lag. A shifted match is always labelled **"Late Settlement /
  Date Shift Match"**, never silently shown as a plain "Matched".
- Every bank line NOT included in the reconciliation is captured in the
  **Parser Audit** with a reason — nothing is silently discarded.
- A terminal seen in the data but absent from the Terminal ID Master is
  never given a guessed store name — it's flagged in **Mapping Review**.

## Saved masters

`POS_Terminal_ID.xlsx` in `data/` is the only file currently wired into
the app (columns: `Terminal ID`, `Store Code`, `Store Name`) — it's used
automatically when no master is uploaded for a run. `Merchant_ID.xlsx` and
`Store_Mapping_FINAL.xlsx` are included for reference but use different
schemas and aren't read by the app yet. Uploading the wrong file into the
Terminal ID Master slot now fails with a clear message instead of a crash.

## Tests

```bash
pip install -r requirements.txt
pytest tests/test_engine.py -v
```

12 regression tests, run against real fixture files in `tests/fixtures/`,
covering: GC scheme separation, master-file validation, the shift-window
default/max/labelling behavior, the Parser Audit, and the Mapping Review
queue. Run these after any change to `engine.py` before redeploying.

## Known deployment gotchas (read this before troubleshooting a "stuck" app)

These cost real time to diagnose the first time around — recorded here so
they don't need rediscovering.

**1. A browser tab left open across a redeploy will show stale state.**
When the server restarts (a new deploy, or a host like Render recycling an
idle instance), all in-memory session data is gone — but a browser tab
that was already open may still *visually* show previously-selected files
in the uploaders, with no indication anything changed. Clicking around in
that tab won't fix it; the file objects are gone server-side even though
they're still listed on screen. **Fix: close the tab completely (or use a
private/incognito window) and re-select files on a genuinely fresh page
load.** A simple in-page refresh is not always enough — session storage
can survive a normal reload. The Month field defaulting to `2026-08` is a
quick tell for whether a page is actually fresh.

**2. Bare ternary expressions as top-level statements trigger Streamlit's
"magic" auto-display**, dumping raw `DeltaGenerator` object reprs onto the
page. E.g. `col.success("x") if cond else col.warning("y")` as a bare
statement gets wrapped in `st.write(...)` because its AST node is an
`IfExp`, not a `Call` — even though both branches are ordinary Streamlit
calls. Always use explicit `if/else` blocks for conditional Streamlit
output instead. This app also ships `.streamlit/config.toml` with
`runner.magicEnabled = false` as a backstop, since nothing here relies on
magic auto-display.

**3. Bulk runs (a POS file per day of the month, dozens at once, plus a
large bank statement) can spike memory.** The header-detection step in
`parse_pos_excel()`/`parse_bank_excel()` used to read every sheet fully
twice; it now uses a cheap `openpyxl` read-only scan of just the first
15-30 rows instead. If a large batch still causes the server process to
restart mid-run, check the host's memory limit/plan and its crash logs —
the app's own `try/except` around the Run button can only catch normal
Python exceptions, not a hard process kill (e.g. an OOM kill), which will
look like the app "forgetting" everything and reverting to a fresh session.

**4. `.streamlit/config.toml` must live in a `.streamlit/` subfolder**, not
the repo root — a `config.toml` sitting at the top level of the repo is
silently ignored by Streamlit.

**5. Free-tier hosting has real resource limits.** On Render's free tier
(0.1 CPU / 512MB RAM), uploading a full month's POS files (30+) all at
once caused the server process to crash outright (exit code 139 -- a hard
native-library crash, visible in Render's Events log, not a normal Python
exception the app could catch and display). Upgrading the Render plan
would fix this directly, but if staying on the free tier: use the "add in
small groups" POS batch workflow (v1.4.0+) -- upload and add 5-10 files at
a time rather than all at once. This keeps peak memory bounded while still
reconciling the complete month in one final pass.

**6. Confirm what's actually deployed before debugging further.** The
caption under the title (`... • v1.4.0`) is the single fastest way to
confirm the live site is running the code you think it is, before spending
time on any other diagnosis. If it doesn't match, the issue is in the
deploy pipeline (branch, build cache, auto-deploy setting), not the code.
