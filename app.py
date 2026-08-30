import io
import pandas as pd
import streamlit as st

from engine import parse_pos_excel, parse_bank_excel, parse_terminal_master, reconcile

APP_VERSION = "v1.2"

st.set_page_config(page_title="SettlementRecon AI", page_icon="🏦", layout="wide")
st.title("SettlementRecon AI")
st.caption(f"Bank & POS Settlement Reconciliation • Monthly Finance Control • {APP_VERSION}")

with st.sidebar:
    st.header("Monthly Run")
    company = st.text_input("Company", "UNITED LUXURY CORP", key="company")
    month = st.text_input("Month", "2026-08", key="month")
    tolerance = st.number_input("Amount tolerance (SAR)", 0.0, 100.0, 1.0, 0.5, key="tolerance")
    max_shift = st.number_input("Maximum settlement date shift (days)", 0, 15, 10, 1, key="max_shift")
    st.divider()
    st.caption("Upload files, run reconciliation, review exceptions, then export the month.")

bank_file = st.file_uploader(
    "Bank Statement (ANB Excel)",
    type=["xlsx"],
    accept_multiple_files=False,
    key="bank_statement_upload",
)

pos_files = st.file_uploader(
    "POS Transaction Reports (multiple Excel files)",
    type=["xlsx"],
    accept_multiple_files=True,
    key="pos_files_upload",
)

terminal_file = st.file_uploader(
    "POS Terminal ID Master (optional — saved master is included)",
    type=["xlsx"],
    accept_multiple_files=False,
    key="terminal_master_upload",
)

bank_ready = bank_file is not None
pos_ready = bool(pos_files) and len(pos_files) > 0
files_ready = bank_ready and pos_ready

status_cols = st.columns(3)
status_cols[0].success("Bank file ready") if bank_ready else status_cols[0].warning("Bank file required")
status_cols[1].success(f"{len(pos_files)} POS file(s) ready") if pos_ready else status_cols[1].warning("POS files required")
status_cols[2].info("Terminal master uploaded") if terminal_file is not None else status_cols[2].info("Using saved terminal master")

run_clicked = st.button(
    "Run Monthly Reconciliation",
    type="primary",
    use_container_width=True,
    disabled=not files_ready,
)

if not files_ready:
    st.info("Upload one ANB bank statement and at least one POS transaction report. The Run button will then become available.")

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
            bank = parse_bank_excel(bank_file.getvalue(), bank_file.name)

            if bank is None or bank.empty:
                st.error("No valid ANB settlement rows were found in the uploaded bank statement.")
                st.stop()

            if terminal_file is not None:
                try:
                    tm = parse_terminal_master(terminal_file.getvalue())
                except Exception as exc:
                    st.error(
                        "The uploaded Terminal ID Master could not be read. "
                        "Please upload the POS Terminal ID master containing Terminal ID, Store Code and Store Name columns."
                    )
                    st.caption(f"Technical detail: {exc}")
                    st.stop()
            else:
                try:
                    with open("data/POS_Terminal_ID.xlsx", "rb") as f:
                        tm = parse_terminal_master(f.read())
                except Exception:
                    tm = pd.DataFrame()

            pos_clean, pagg, bank_sett, recon = reconcile(
                pos, bank, tm, float(tolerance), float(max_shift)
            )

            if recon is None or recon.empty:
                st.error("Reconciliation completed but produced no reconciliation rows. Please review the uploaded period/files.")
                st.stop()

            st.session_state["run"] = {
                "pos_clean": pos_clean,
                "pagg": pagg,
                "bank_sett": bank_sett,
                "recon": recon,
                "tm": tm,
                "company": company,
                "month": month,
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
    run_company = run["company"]
    run_month = run["month"]

    total_pos = float(recon["pos_gross"].sum())
    total_bank = float(recon["gross_credit"].sum())
    diff = total_pos - total_bank
    matched = int(recon["status"].isin(["Matched", "Date Shift Match"]).sum())
    total = len(recon)
    match_pct = matched / total * 100 if total else 0

    date_summary = (
        recon.assign(report_date=recon["pos_date"].fillna(recon["settlement_date"]))
        .groupby("report_date", dropna=False, as_index=False)
        .agg(
            POS_Gross=("pos_gross", "sum"),
            Bank_Gross=("gross_credit", "sum"),
            Fee=("fee", "sum"),
            VAT=("vat", "sum"),
            Net_Bank=("net_settlement", "sum"),
            Difference=("gross_difference", "sum"),
            Records=("status", "size"),
        )
    )

    store_summary = (
        recon.groupby(["store_code", "store_name"], dropna=False, as_index=False)
        .agg(
            POS_Gross=("pos_gross", "sum"),
            Bank_Gross=("gross_credit", "sum"),
            Fee=("fee", "sum"),
            VAT=("vat", "sum"),
            Net_Bank=("net_settlement", "sum"),
            Difference=("gross_difference", "sum"),
            Records=("status", "size"),
        )
    )

    date_store_summary = (
        recon.assign(report_date=recon["pos_date"].fillna(recon["settlement_date"]))
        .groupby(["report_date", "store_code", "store_name"], dropna=False, as_index=False)
        .agg(
            POS_Gross=("pos_gross", "sum"),
            Bank_Gross=("gross_credit", "sum"),
            Fee=("fee", "sum"),
            VAT=("vat", "sum"),
            Net_Bank=("net_settlement", "sum"),
            Difference=("gross_difference", "sum"),
        )
    )

    exceptions = recon[~recon["status"].isin(["Matched", "Date Shift Match"])]

    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        summary = pd.DataFrame(
            {
                "Metric": [
                    "Company", "Month", "POS Gross", "Bank Gross", "Difference",
                    "Matched / Date Shift", "Total Recon Rows", "Match %"
                ],
                "Value": [
                    run_company, run_month, total_pos, total_bank, diff,
                    matched, total, match_pct
                ],
            }
        )
        summary.to_excel(w, "Dashboard", index=False)
        recon.to_excel(w, "Detailed_Recon", index=False)
        date_summary.to_excel(w, "Date_Summary", index=False)
        store_summary.to_excel(w, "Store_Summary", index=False)
        date_store_summary.to_excel(w, "Date_Store_Summary", index=False)
        exceptions.to_excel(w, "Exceptions", index=False)
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

    total_pos = float(recon["pos_gross"].sum())
    total_bank = float(recon["gross_credit"].sum())
    diff = total_pos - total_bank
    matched = int(recon["status"].isin(["Matched", "Date Shift Match"]).sum())
    total = len(recon)
    match_pct = matched / total * 100 if total else 0

    if "excel_bytes" not in st.session_state:
        excel_bytes, date_summary, store_summary, ds = build_excel(run)
        st.session_state["excel_bytes"] = excel_bytes
    else:
        excel_bytes = st.session_state["excel_bytes"]
        # Rebuild lightweight summaries for display.
        _, date_summary, store_summary, ds = build_excel(run)

    st.subheader("Reconciliation Results")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("POS Gross", f"SAR {total_pos:,.2f}")
    c2.metric("Bank Gross", f"SAR {total_bank:,.2f}")
    c3.metric("Difference", f"SAR {diff:,.2f}")
    c4.metric("Matched / Date Shift", f"{matched:,}")
    c5.metric("Match %", f"{match_pct:.1f}%")

    st.download_button(
        "⬇️ Download Final Monthly Excel Report",
        data=excel_bytes,
        file_name=f"SettlementRecon_{run['month']}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        use_container_width=True,
        key="download_monthly_report_top",
    )

    tabs = st.tabs([
        "Dashboard", "Detailed Recon", "Date-wise", "Store-wise",
        "Date + Store", "Exceptions", "Raw Control", "Export"
    ])

    with tabs[0]:
        st.subheader("Monthly Settlement Control")
        s = recon.groupby("status", as_index=False).agg(
            Records=("status", "size"),
            POS_Gross=("pos_gross", "sum"),
            Bank_Gross=("gross_credit", "sum"),
            Difference=("gross_difference", "sum"),
        )
        st.dataframe(s, use_container_width=True, hide_index=True)
        st.bar_chart(s.set_index("status")["Records"])

    with tabs[1]:
        cols = [
            "settlement_date", "pos_date", "bank_posting_date", "store_code",
            "store_name", "retailer_id", "terminal_id", "scheme_group",
            "card_scheme", "pos_tx", "transaction_count", "pos_gross",
            "gross_credit", "fee", "vat", "net_settlement",
            "gross_difference", "date_shift_days", "status", "bank_tx_id"
        ]
        st.dataframe(
            recon[[c for c in cols if c in recon.columns]],
            use_container_width=True,
            hide_index=True,
        )

    with tabs[2]:
        st.dataframe(date_summary, use_container_width=True, hide_index=True)

    with tabs[3]:
        st.dataframe(store_summary, use_container_width=True, hide_index=True)

    with tabs[4]:
        st.dataframe(ds, use_container_width=True, hide_index=True)

    with tabs[5]:
        st.dataframe(
            recon[~recon["status"].isin(["Matched", "Date Shift Match"])],
            use_container_width=True,
            hide_index=True,
        )

    with tabs[6]:
        st.write(f"POS rows after deduplication: **{len(pos_clean):,}**")
        st.write(f"POS daily terminal/scheme groups: **{len(pagg):,}**")
        st.write(f"Bank settlement credit rows: **{len(bank_sett):,}**")
        st.dataframe(bank_sett.head(200), use_container_width=True, hide_index=True)

    with tabs[7]:
        st.success("Your reconciliation is ready for export.")
        st.download_button(
            "⬇️ Download Final Monthly Excel Report",
            data=excel_bytes,
            file_name=f"SettlementRecon_{run['month']}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
            key="download_monthly_report_export_tab",
        )
