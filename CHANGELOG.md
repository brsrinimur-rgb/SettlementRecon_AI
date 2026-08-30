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
