# Changelog

## v1.1 (patch on top of v1.0 working base)

Additive fixes only — no rewrite, no change to file layout, no change to the
overall Streamlit workflow. All four requested fixes plus the two follow-on
corrections, verified against your real bank statement + POS export + master
files, with a regression test suite locking each one in.

### 1. `POS GC_...` scheme classification fixed
Previously any narration not matching `' MD_'` defaulted to `'CC'`, so the 3
`POS GC_` settlement batches in your real August statement (SAR 5,522 total)
were silently merged into your Credit Card totals with no POS-side record
behind them. `engine.py` now maps scheme codes explicitly
(`SCHEME_CODE_MAP = {'MD': 'MADA', 'CC': 'CC', 'GC': 'GCC'}`) and routes
anything not in that map to the Parser Audit instead of guessing. GC now
totals separately: `Bank Gross (GCC) = 5,522.00`, and CC totals dropped by
exactly that amount.

### 2. Master-file validation
`parse_terminal_master()` now checks for the required columns
(`Terminal ID`, `Store Code`, `Store Name`) and raises a `MasterFileError`
with a message naming what's missing, instead of a bare `KeyError`.
`app.py` catches it and shows `st.error(...)` instead of crashing. Verified:
uploading `Merchant_ID.xlsx` (different schema) now fails gracefully with
"This is not a Terminal ID Master...".

### 3. Settlement date-shift window widened
Default `max_date_shift` was 3, UI capped it at 7. Real ANB settlement lag
observed in your data goes up to 9 days, so legitimate late settlements were
being misclassified as "Missing POS." New default is **10 days**, UI range
**0–15**. `reconcile()` clamps any out-of-range value rather than erroring.
A date-shifted match is still always labelled `Late Settlement / Date Shift
Match` — never silently shown as plain `Matched` (this is enforced by a
regression test).

### 4. Parser Audit — dropped bank rows are now visible
`parse_bank_excel()` returns `(credits, audit)` instead of just `credits`.
Every bank statement line not included in the reconciliation is now captured
with a reason: `Non-POS bank activity`, `Amex direct settlement (no
POS-detail source configured yet)`, `Unrecognized POS scheme code`, or
`Could not parse Retailer ID / Terminal ID / Date from Narration 2`. New
**Parser Audit** tab in the app and `Parser_Audit` sheet in the Excel export.

### Also included, as discussed
- **Mapping Review queue**: a terminal seen in the data but absent from the
  Terminal ID Master is never given a guessed name. It's flagged with
  `(Unmapped -- Terminal <id>, needs review)` and listed in a new **Mapping
  Review** tab / `Mapping_Review` export sheet. Verified against terminal
  `55610691`, which is genuinely absent from `POS_Terminal_ID.xlsx`.
- **Scheme separation preserved end-to-end**: `MADA`, `CC` (Visa/Mastercard —
  the bank doesn't settle them separately, so `card_scheme` still records
  which one for audit purposes), `GCC`, and Amex is left in the Parser Audit
  rather than guessed at, since there's no POS-side source file for it yet.
- **`tests/test_engine.py`**: 12 regression tests, all passing, covering all
  four fixes plus the mapping-review behavior. Run with
  `pytest tests/test_engine.py -v`. Uses real sample files as fixtures
  (`tests/fixtures/`).

### Not changed
File layout, `run_app.bat`, the overall tab structure (Dashboard through Raw
Control), dedup logic, POS parsing, and the core date-shift matching
algorithm are untouched from v1.0.

## v1.2 (app.py corrected for actual v1.1 engine compatibility)

A separately-drafted `app.py` was proposed for a GitHub/Render deployment.
It was written against an older `engine.py` than what's actually in this
repo, so applying it as-is would have broken the app on redeploy:

- `bank = parse_bank_excel(...)` — v1.1's `parse_bank_excel()` returns a
  `(credits, audit)` tuple, not a single DataFrame. The proposed file
  assigned the tuple to `bank` then called `bank.empty` →
  `AttributeError: 'tuple' object has no attribute 'empty'`. Verified by
  running it against the real v1.1 `engine.py`.
- `pos_clean, pagg, bank_sett, recon = reconcile(...)` — v1.1's
  `reconcile()` returns 5 values (it added `mapping_review`). Unpacking
  into 4 variables → `ValueError: too many values to unpack`. Also verified.
- It checked `status.isin(["Matched", "Date Shift Match"])`, but v1.1
  renamed that status to `"Late Settlement / Date Shift Match"` — had it
  run, every date-shifted match would have silently counted as an
  exception instead of a match, with no error to flag it.

`app.py` has been corrected to actually match the v1.1 `engine.py` function
signatures, while keeping the genuinely good parts of the proposed
redesign:

- Run button disabled until both bank file and POS files are received
  (rather than clickable-then-error).
- Live "N POS file(s) ready" / "Bank file ready" status row, so upload
  completion is visible before you click Run — addresses the earlier race
  condition between file-select and upload-complete on large multi-file
  batches.
- Full run state (including company/month and now also `mapping_review`
  and `bank_audit`) cached in `st.session_state`, and the built Excel bytes
  cached separately so re-rendering the page doesn't silently lose the
  download or rebuild it from stale variables.
- Download button available immediately after a successful run, and again
  in a dedicated Export tab.
- Whole-run try/except with `st.exception()` so a failure shows the real
  traceback instead of a blank page.

Everything from v1.1 is preserved: GC kept separate from CC (with a scheme
totals table on the Dashboard tab as a standing contamination check),
Parser Audit tab, Mapping Review tab, the 10-day default / 15-day max
settlement shift window, and master-file validation. `engine.py` is
unchanged from v1.1 — this was purely an `app.py` fix, verified against the
real `engine.py` and the 12 existing regression tests (all still passing,
since nothing in `engine.py` moved).

## v1.2.1 (magic-write dump fixed)

Deploying v1.2 to the browser showed raw `DeltaGenerator` object reprs
(class docstring, method list, and all) dumped onto the page above the
upload status boxes. Root cause: three status lines used a bare ternary as
a statement, e.g.

```python
status_cols[0].success("Bank file ready") if bank_ready else status_cols[0].warning("Bank file required")
```

Streamlit's "magic" feature auto-wraps any bare top-level expression whose
AST node isn't a plain `Call` in `st.write(...)`. A ternary's node type is
`IfExp`, not `Call`, so even though both branches are ordinary Streamlit
calls, magic wrapped the whole expression — executing the call (so the
alert box did show) *and* additionally calling `st.write()` on its
`DeltaGenerator` return value (the garbage repr). Every other line in the
file is a plain `Call` statement and was never affected — confirmed by the
screenshot only showing garbage above those three specific boxes.

Fixed by converting all three to explicit `if/else` blocks, so each branch
is its own plain `Call` statement (the same pattern already used correctly
everywhere else in the file). Also added `.streamlit/config.toml` with
`runner.magicEnabled = false` as a safety net — nothing in this app relies
on magic auto-display, so disabling it removes the whole class of bug for
any future edit. Verified: app boots cleanly with magic disabled, and all
12 regression tests still pass (engine.py untouched).
