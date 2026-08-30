# SettlementRecon AI
Standalone monthly Bank & POS Settlement Reconciliation application.

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
- Bank: parses ANB POS narration for Retailer ID, Terminal ID, embedded settlement date, scheme, fee, VAT and transaction count.
- Duplicate POS extracts are removed using a transaction-level fingerprint.
- Matching is Terminal + Retailer + Scheme with embedded settlement date and controlled date-shift search.
- It never force-matches a settlement outside configured date tolerance; exceptions stay visible.

## Saved masters
The package includes the supplied Terminal ID, Merchant ID and Store Mapping master files in `data/`. The Terminal ID master is used automatically when no new master is uploaded. Replace/update these masters when stores or terminals change.
