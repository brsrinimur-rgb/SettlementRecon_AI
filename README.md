# SettlementRecon AI (v1.1)
Standalone monthly Bank & POS Settlement Reconciliation application.

See `CHANGELOG.md` for what changed in v1.1 (GC scheme fix, master-file
validation, wider settlement-shift window, Parser Audit, Mapping Review).

## Run on Windows
1. Extract the package.
2. Double-click `run_app.bat`.
3. Browser opens automatically.
4. Upload monthly ANB bank statement, all POS transaction reports, and POS Terminal ID master.
5. Click **Run Monthly Reconciliation**.
6. Review Dashboard, Detailed Recon, Date-wise, Store-wise, Date + Store and Exceptions.
7. Download the final Excel report.

## Matching control
- POS files: reads `Details_mada` and `Details_CC`.
- Bank: parses ANB POS narration for Retailer ID, Terminal ID, embedded settlement date, scheme (MADA / CC / GCC), fee, VAT and transaction count.
- Duplicate POS extracts are removed using a transaction-level fingerprint.
- Matching is Terminal + Retailer + Scheme with embedded settlement date and controlled date-shift search (default 10 days, 0–15 configurable).
- It never force-matches a settlement outside configured date tolerance; exceptions stay visible. A date-shifted match is always labelled `Late Settlement / Date Shift Match`, never silently shown as plain `Matched`.
- Every bank line not included in the reconciliation is captured with a reason in the **Parser Audit** tab — nothing is silently discarded.

## Saved masters
The package includes the supplied Terminal ID, Merchant ID and Store Mapping master files in `data/`.

**Only `POS_Terminal_ID.xlsx` is currently wired into the app** — it's the file the
Terminal ID Master uploader (and the automatic fallback) expects, with columns
`Terminal ID` / `Store Code` / `Store Name`. `Merchant_ID.xlsx` and
`Store_Mapping_FINAL.xlsx` use different schemas (Merchant-Name-based, and
Provider-Name→D365-name based) and aren't read by the app yet — uploading
either of them into the Terminal ID Master slot now fails with a clear message
instead of crashing. Wiring those two in as their own mapping stage is a
natural next addition.

A terminal that appears in the bank statement or POS files but isn't in the
Terminal ID Master is never given a guessed store name — it's flagged in the
**Mapping Review** tab for you to confirm and add to the master.

## Tests
```bash
pip install -r requirements.txt
pytest tests/test_engine.py -v
```
12 regression tests covering the v1.1 fixes, run against real sample files in
`tests/fixtures/`.
