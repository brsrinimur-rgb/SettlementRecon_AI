import io
import time
import uuid
import pandas as pd
import streamlit as st

from engine import (parse_pos_excel, parse_bank_excel, parse_terminal_master, reconcile,
                     MasterFileError, DEFAULT_MAX_DATE_SHIFT, MAX_ALLOWED_DATE_SHIFT)

APP_VERSION = "v1.2.4"
MATCHED_STATUSES = ["Matched", "Late Settlement / Date Shift Match"]


@st.cache_resource
def _server_boot_info():
    """Runs once per live server process (cached across reruns AND across browser
    sessions hitting the same process), not once per script rerun. If two
    screenshots taken minutes apart show a DIFFERENT boot id / boot time, the
    browser reconnected to a different server process in between (e.g. a
    redeploy restarted the container, or Render routed the request to a
    different instance) -- a strong signal the browser tab's session went stale
    without a full page reload. If the boot id stays the same, that's ruled out."""
    return {"boot_id": uuid.uuid4().hex[:8], "boot_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}


st.set_page_config(page_title="SettlementRecon AI", page_icon="🏦", layout="wide")
st.title("SettlementRecon AI")
st.caption(f"Bank & POS Settlement Reconciliation • Monthly Finance Control • {APP_VERSION}")

boot_info = _server_boot_info()

with st.sidebar:
    st.header("Monthly Run")
    company = st.text_input("Company", "UNITED LUXURY CORP", key="company")
    month = st.text_input("Month", "2026-08", key="month")
    tolerance = st.number_input("Amount tolerance (SAR)", 0.0, 100.0, 1.0, 0.5, key="tolerance")
    max_shift = st.number_input("Maximum settlement date shift (days)", 0, MAX_ALLOWED_DATE_SHIFT,
                                 DEFAULT_MAX_DATE_SHIFT, 1, key="max_shift",
                                 help='ANB settlement can lag several days. A match found via date shift is '
                                      'always labelled "Late Settlement / Date Shift Match", never silently '
                                      'shown as a plain Matched.')
    st.divider()
    st.caption("Upload files, run reconciliation, review exceptions, then export the month.")

bank_file = st.file_uploader(
    "Bank Statement (ANB Excel)", type=["xlsx"], accept_multiple_files=False, key="bank_statement_upload",
)
pos_files = st.file_uploader(
    "POS Transaction Reports (multiple Excel files)", type=["xlsx"], accept_multiple_files=True,
    key="pos_files_upload",
)
terminal_file = st.file_uploader(
    "POS Terminal ID Master (optional — saved master is included)", type=["xlsx"], accept_multiple_files=False,
    key="terminal_master_upload",
)

bank_ready = bank_file is not None
pos_ready = bool(pos_files) and len(pos_files) > 0
files_ready = bank_ready and pos_ready

with st.expander("🔧 Debug info (expand and screenshot this if uploads aren't registering)", expanded=False):
    st.code(
        f"Server boot id:     {boot_info['boot_id']}\n"
        f"Server started at:  {boot_info['boot_time']}\n"
        f"This render at:     {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n"
        f"Streamlit version:  {st.__version__}\n"
        f"\n"
        f"bank_file:     {type(bank_file).__name__} -- {'None' if bank_file is None else bank_file.name}\n"
        f"pos_files:     {type(pos_files).__name__} with {len(pos_files) if pos_files else 0} item(s)\n"
        f"terminal_file: {type(terminal_file).__name__} -- {'None' if terminal_file is None else terminal_file.name}\n"
        f"bank_ready={bank_ready}  pos_ready={pos_ready}  files_ready={files_ready}",
        language="text",
    )
    st.caption("If 'Server boot id' differs across two screenshots taken close together, the browser "
               "reconnected to a different/restarted server process between them -- reload the page "
               "fully (not just click within it) to get a clean session.")

# Live readout of what the backend has actually received -- the uploader widget shows
# a filename the instant it's picked in the browser, before the upload to the server
# finishes. With many files (a daily POS export per day of the month) that gap is
# real; this makes the true received state visible instead of trusting the widget.
status_cols = st.columns(3)
if bank_ready:
    status_cols[0].success("Bank file ready")
else:
    status_cols[0].warning("Bank file required")

if pos_ready:
    status_cols[1].success(f"{len(pos_files)} POS file(s) ready")
else:
    status_cols[1].warning("POS files required")

if terminal_file is not None:
    status_cols[2].info("Terminal master uploaded")
else:
    status_cols[2].info("Using saved terminal master")

# Files can finish uploading to the server in the background, after the browser
# already shows them selected. Normally that completion triggers an automatic
# rerun and the status above updates on its own -- but if that event is ever
# missed or delayed (a dropped websocket frame, several large simultaneous
# uploads, a proxy in front of the app buffering), the Run button below can be
# stuck disabled with no interaction able to re-check it, since a disabled
# button can't be clicked. This button is deliberately never disabled: clicking
# it does nothing but force a fresh rerun, which re-reads the uploaders' actual
# current state from the server -- the only way out of that stuck state besides
# reloading the page.
st.button("🔄 Refresh upload status (click if files are shown above but still marked as required)")

run_clicked = st.button(
    "Run Monthly Reconciliation", type="primary", use_container_width=True, disabled=not files_ready,
)
if not files_ready:
    st.info("Upload one ANB bank statement and at least one POS transaction report. "
            "The Run button will then become available.")

if run_clicked:
    try:
        with st.spinner("Processing monthly files..."):
            pos_parts = []
            for f in pos_files:
                x = parse_pos_excel(f.getvalue(), f.name)
                if x is not None and not x.empty:
                    pos_parts.append(x)
            if not pos_parts:
                st.error("No valid POS transaction rows were found in the uploaded POS files.")
                st.stop()
            pos = pd.concat(pos_parts, ignore_index=True)

            bank, bank_audit = parse_bank_excel(bank_file.getvalue(), bank_file.name)
            if bank is None or bank.empty:
                st.error("No valid ANB settlement rows were found in the uploaded bank statement.")
                st.stop()

            if terminal_file is not None:
                try:
                    tm = parse_terminal_master(terminal_file.getvalue())
                except MasterFileError as exc:
                    st.error(f"⚠ {exc}")
                    st.stop()
            else:
                try:
                    with open("data/POS_Terminal_ID.xlsx", "rb") as f:
                        tm = parse_terminal_master(f.read())
                except MasterFileError as exc:
                    st.warning(f"Saved default master could not be used: {exc}")
                    tm = pd.DataFrame()
                except Exception:
                    tm = pd.DataFrame()

            pos_clean, pagg, bank_sett, recon, mapping_review = reconcile(
                pos, bank, tm, float(tolerance), float(max_shift)
            )
            if recon is None or recon.empty:
                st.error("Reconciliation completed but produced no reconciliation rows. Please review the uploaded period/files.")
                st.stop()

            st.session_state["run"] = {
                "pos_clean": pos_clean, "pagg": pagg, "bank_sett": bank_sett, "recon": recon,
                "tm": tm, "mapping_review": mapping_review, "bank_audit": bank_audit,
                "company": company, "month": month,
            }
            # Remove any workbook cached from an earlier run.
            st.session_state.pop("excel_bytes", None)
            st.success("Monthly reconciliation completed successfully.")

    except Exception as exc:
        st.error("Reconciliation failed. The uploaded files were kept unchanged.")
        st.exception(exc)


def build_excel(run):
    pos_clean = run["pos_clean"]
    bank_sett = run["bank_sett"]
    recon = run["recon"]
    tm = run["tm"]
    mapping_review = run["mapping_review"]
    bank_audit = run["bank_audit"]
    run_company = run["company"]
    run_month = run["month"]

    total_pos = float(recon["pos_gross"].sum())
    total_bank = float(recon["gross_credit"].sum())
    diff = total_pos - total_bank
    matched = int(recon["status"].isin(MATCHED_STATUSES).sum())
    total = len(recon)
    match_pct = matched / total * 100 if total else 0

    date_summary = (
        recon.assign(report_date=recon["pos_date"].fillna(recon["settlement_date"]))
        .groupby("report_date", dropna=False, as_index=False)
        .agg(POS_Gross=("pos_gross", "sum"), Bank_Gross=("gross_credit", "sum"), Fee=("fee", "sum"),
             VAT=("vat", "sum"), Net_Bank=("net_settlement", "sum"), Difference=("gross_difference", "sum"),
             Records=("status", "size"))
    )
    store_summary = (
        recon.groupby(["store_code", "store_name"], dropna=False, as_index=False)
        .agg(POS_Gross=("pos_gross", "sum"), Bank_Gross=("gross_credit", "sum"), Fee=("fee", "sum"),
             VAT=("vat", "sum"), Net_Bank=("net_settlement", "sum"), Difference=("gross_difference", "sum"),
             Records=("status", "size"))
    )
    date_store_summary = (
        recon.assign(report_date=recon["pos_date"].fillna(recon["settlement_date"]))
        .groupby(["report_date", "store_code", "store_name"], dropna=False, as_index=False)
        .agg(POS_Gross=("pos_gross", "sum"), Bank_Gross=("gross_credit", "sum"), Fee=("fee", "sum"),
             VAT=("vat", "sum"), Net_Bank=("net_settlement", "sum"), Difference=("gross_difference", "sum"))
    )
    exceptions = recon[~recon["status"].isin(MATCHED_STATUSES)]

    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        summary = pd.DataFrame({
            "Metric": ["Company", "Month", "POS Gross", "Bank Gross", "Difference",
                       "Matched / Late Settlement", "Total Recon Rows", "Match %",
                       "Terminals Needing Mapping", "Dropped Bank Lines"],
            "Value": [run_company, run_month, total_pos, total_bank, diff, matched, total, match_pct,
                      len(mapping_review), 0 if bank_audit is None else len(bank_audit)],
        })
        summary.to_excel(w, "Dashboard", index=False)
        recon.to_excel(w, "Detailed_Recon", index=False)
        date_summary.to_excel(w, "Date_Summary", index=False)
        store_summary.to_excel(w, "Store_Summary", index=False)
        date_store_summary.to_excel(w, "Date_Store_Summary", index=False)
        exceptions.to_excel(w, "Exceptions", index=False)
        pd.DataFrame({"Terminal ID": mapping_review}).to_excel(w, "Mapping_Review", index=False)
        if bank_audit is not None and not bank_audit.empty:
            bank_audit.to_excel(w, "Parser_Audit", index=False)
        bank_sett.to_excel(w, "Bank_Settlements", index=False)
        pos_clean.to_excel(w, "POS_Normalized", index=False)
        if tm is not None and not tm.empty:
            tm.to_excel(w, "Terminal_Master", index=False)

    return bio.getvalue(), date_summary, store_summary, date_store_summary


if "run" in st.session_state:
    run = st.session_state["run"]
    pos_clean = run["pos_clean"]
    pagg = run["pagg"]
    bank_sett = run["bank_sett"]
    recon = run["recon"]
    mapping_review = run["mapping_review"]
    bank_audit = run["bank_audit"]

    total_pos = float(recon["pos_gross"].sum())
    total_bank = float(recon["gross_credit"].sum())
    diff = total_pos - total_bank
    matched = int(recon["status"].isin(MATCHED_STATUSES).sum())
    total = len(recon)
    match_pct = matched / total * 100 if total else 0

    if "excel_bytes" not in st.session_state:
        excel_bytes, date_summary, store_summary, ds = build_excel(run)
        st.session_state["excel_bytes"] = excel_bytes
    else:
        excel_bytes = st.session_state["excel_bytes"]
        _, date_summary, store_summary, ds = build_excel(run)  # lightweight summaries for display only

    st.subheader("Reconciliation Results")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("POS Gross", f"SAR {total_pos:,.2f}")
    c2.metric("Bank Gross", f"SAR {total_bank:,.2f}")
    c3.metric("Difference", f"SAR {diff:,.2f}")
    c4.metric("Matched / Late Settlement", f"{matched:,}")
    c5.metric("Match %", f"{match_pct:.1f}%")

    if mapping_review:
        st.warning(f"⚠ {len(mapping_review)} terminal(s) are not in the Terminal ID Master and need review: "
                   + ", ".join(mapping_review))

    st.download_button(
        "⬇️ Download Final Monthly Excel Report", data=excel_bytes,
        file_name=f"SettlementRecon_{run['month']}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", use_container_width=True, key="download_monthly_report_top",
    )

    tabs = st.tabs(["Dashboard", "Detailed Recon", "Date-wise", "Store-wise", "Date + Store",
                     "Exceptions", "Mapping Review", "Parser Audit", "Raw Control", "Export"])

    with tabs[0]:
        st.subheader("Monthly Settlement Control")
        s = recon.groupby("status", as_index=False).agg(
            Records=("status", "size"), POS_Gross=("pos_gross", "sum"),
            Bank_Gross=("gross_credit", "sum"), Difference=("gross_difference", "sum"))
        st.dataframe(s, use_container_width=True, hide_index=True)
        st.bar_chart(s.set_index("status")["Records"])
        st.caption("Scheme totals (contamination check — GC should never appear inside CC):")
        scheme_totals = recon.groupby("scheme_group", as_index=False).agg(
            POS_Gross=("pos_gross", "sum"), Bank_Gross=("gross_credit", "sum"), Records=("status", "size"))
        st.dataframe(scheme_totals, use_container_width=True, hide_index=True)

    with tabs[1]:
        cols = ["settlement_date", "pos_date", "bank_posting_date", "store_code", "store_name",
                "retailer_id", "terminal_id", "scheme_group", "card_scheme", "pos_tx",
                "transaction_count", "pos_gross", "gross_credit", "fee", "vat", "net_settlement",
                "gross_difference", "date_shift_days", "status", "bank_tx_id"]
        st.dataframe(recon[[c for c in cols if c in recon.columns]], use_container_width=True, hide_index=True)

    with tabs[2]:
        st.dataframe(date_summary, use_container_width=True, hide_index=True)

    with tabs[3]:
        st.dataframe(store_summary, use_container_width=True, hide_index=True)

    with tabs[4]:
        st.dataframe(ds, use_container_width=True, hide_index=True)

    with tabs[5]:
        st.dataframe(recon[~recon["status"].isin(MATCHED_STATUSES)], use_container_width=True, hide_index=True)

    with tabs[6]:
        st.subheader("Terminals needing a store mapping")
        st.caption("These terminal IDs appeared in the bank statement or POS files but are not in the "
                   "Terminal ID Master. No store name has been guessed — add them to the master once confirmed.")
        if mapping_review:
            st.dataframe(pd.DataFrame({"Terminal ID": mapping_review}), use_container_width=True, hide_index=True)
        else:
            st.success("Every terminal seen this run is mapped to a store.")

    with tabs[7]:
        st.subheader("Dropped / unrecognized bank statement lines")
        st.caption("Every bank line NOT included in the reconciliation, with the reason. Nothing is silently discarded.")
        if bank_audit is not None and not bank_audit.empty:
            reason_counts = bank_audit.groupby("reason", as_index=False).size().rename(columns={"size": "Count"})
            st.dataframe(reason_counts, use_container_width=True, hide_index=True)
            st.dataframe(bank_audit, use_container_width=True, hide_index=True)
        else:
            st.success("No bank lines were dropped this run.")

    with tabs[8]:
        st.write(f"POS rows after deduplication: **{len(pos_clean):,}**")
        st.write(f"POS daily terminal/scheme groups: **{len(pagg):,}**")
        st.write(f"Bank settlement credit rows: **{len(bank_sett):,}**")
        st.dataframe(bank_sett.head(200), use_container_width=True, hide_index=True)

    with tabs[9]:
        st.success("Your reconciliation is ready for export.")
        st.download_button(
            "⬇️ Download Final Monthly Excel Report", data=excel_bytes,
            file_name=f"SettlementRecon_{run['month']}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary", use_container_width=True, key="download_monthly_report_export_tab",
        )
