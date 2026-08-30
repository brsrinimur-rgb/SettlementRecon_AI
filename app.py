import io, zipfile
from datetime import datetime
import pandas as pd
import streamlit as st
from engine import parse_pos_excel, parse_bank_excel, parse_terminal_master, reconcile

st.set_page_config(page_title='SettlementRecon AI', page_icon='🏦', layout='wide')
st.title('SettlementRecon AI')
st.caption('Bank & POS Settlement Reconciliation • Monthly Finance Control')

with st.sidebar:
    st.header('Monthly Run')
    company=st.text_input('Company','UNITED LUXURY CORP')
    month=st.text_input('Month','2026-08')
    tolerance=st.number_input('Amount tolerance (SAR)',0.0,100.0,1.0,0.5)
    max_shift=st.number_input('Maximum settlement date shift',0,7,3,1)
    st.divider()
    st.caption('Upload files, run reconciliation, review exceptions, then export the month.')

bank_file=st.file_uploader('1. Bank Statement (ANB Excel)',type=['xlsx'],accept_multiple_files=False)
pos_files=st.file_uploader('2. POS Transaction Reports (multiple Excel files)',type=['xlsx'],accept_multiple_files=True)
terminal_file=st.file_uploader('3. POS Terminal ID Master (optional — saved master is included)',type=['xlsx'],accept_multiple_files=False)

if st.button('Run Monthly Reconciliation',type='primary',use_container_width=True):
    if not bank_file or not pos_files:
        st.error('Upload the bank statement and POS files first.')
        st.stop()
    with st.spinner('Processing monthly files...'):
        pos_parts=[]
        for f in pos_files:
            x=parse_pos_excel(f.getvalue(),f.name)
            if not x.empty: pos_parts.append(x)
        pos=pd.concat(pos_parts,ignore_index=True) if pos_parts else pd.DataFrame()
        bank=parse_bank_excel(bank_file.getvalue(),bank_file.name)
        
        if terminal_file:
            tm=parse_terminal_master(terminal_file.getvalue())
        else:
            try:
                with open('data/POS_Terminal_ID.xlsx','rb') as f: tm=parse_terminal_master(f.read())
            except Exception:
                tm=pd.DataFrame()
        pos_clean,pagg,bank_sett,recon=reconcile(pos,bank,tm,tolerance,float(max_shift))
        st.session_state['run']=(pos_clean,pagg,bank_sett,recon)

if 'run' in st.session_state:
    pos_clean,pagg,bank_sett,recon=st.session_state['run']
    total_pos=recon.pos_gross.sum(); total_bank=recon.gross_credit.sum(); diff=total_pos-total_bank
    matched=recon.status.isin(['Matched','Date Shift Match']).sum(); total=len(recon); match_pct=(matched/total*100 if total else 0)
    c1,c2,c3,c4,c5=st.columns(5)
    c1.metric('POS Gross',f'SAR {total_pos:,.2f}')
    c2.metric('Bank Gross',f'SAR {total_bank:,.2f}')
    c3.metric('Difference',f'SAR {diff:,.2f}')
    c4.metric('Matched / Date Shift',f'{matched:,}')
    c5.metric('Match %',f'{match_pct:.1f}%')

    tabs=st.tabs(['Dashboard','Detailed Recon','Date-wise','Store-wise','Date + Store','Exceptions','Raw Control'])
    with tabs[0]:
        st.subheader('Monthly Settlement Control')
        s=recon.groupby('status',as_index=False).agg(Records=('status','size'),POS_Gross=('pos_gross','sum'),Bank_Gross=('gross_credit','sum'),Difference=('gross_difference','sum'))
        st.dataframe(s,use_container_width=True,hide_index=True)
        st.bar_chart(s.set_index('status')['Records'])
    with tabs[1]:
        cols=['settlement_date','pos_date','bank_posting_date','store_code','store_name','retailer_id','terminal_id','scheme_group','card_scheme','pos_tx','transaction_count','pos_gross','gross_credit','fee','vat','net_settlement','gross_difference','date_shift_days','status','bank_tx_id']
        st.dataframe(recon[[c for c in cols if c in recon.columns]],use_container_width=True,hide_index=True)
    date_summary=recon.assign(report_date=recon['pos_date'].fillna(recon['settlement_date'])).groupby('report_date',dropna=False,as_index=False).agg(POS_Gross=('pos_gross','sum'),Bank_Gross=('gross_credit','sum'),Fee=('fee','sum'),VAT=('vat','sum'),Net_Bank=('net_settlement','sum'),Difference=('gross_difference','sum'),Records=('status','size'))
    with tabs[2]: st.dataframe(date_summary,use_container_width=True,hide_index=True)
    store_summary=recon.groupby(['store_code','store_name'],dropna=False,as_index=False).agg(POS_Gross=('pos_gross','sum'),Bank_Gross=('gross_credit','sum'),Fee=('fee','sum'),VAT=('vat','sum'),Net_Bank=('net_settlement','sum'),Difference=('gross_difference','sum'),Records=('status','size'))
    with tabs[3]: st.dataframe(store_summary,use_container_width=True,hide_index=True)
    ds=recon.assign(report_date=recon['pos_date'].fillna(recon['settlement_date'])).groupby(['report_date','store_code','store_name'],dropna=False,as_index=False).agg(POS_Gross=('pos_gross','sum'),Bank_Gross=('gross_credit','sum'),Fee=('fee','sum'),VAT=('vat','sum'),Net_Bank=('net_settlement','sum'),Difference=('gross_difference','sum'))
    with tabs[4]: st.dataframe(ds,use_container_width=True,hide_index=True)
    with tabs[5]: st.dataframe(recon[~recon.status.isin(['Matched','Date Shift Match'])],use_container_width=True,hide_index=True)
    with tabs[6]:
        st.write(f'POS rows after deduplication: **{len(pos_clean):,}**')
        st.write(f'POS daily terminal/scheme groups: **{len(pagg):,}**')
        st.write(f'Bank settlement credit rows: **{len(bank_sett):,}**')
        st.dataframe(bank_sett.head(200),use_container_width=True,hide_index=True)

    def make_excel():
        bio=io.BytesIO()
        with pd.ExcelWriter(bio,engine='openpyxl') as w:
            summary=pd.DataFrame({'Metric':['Company','Month','POS Gross','Bank Gross','Difference','Matched / Date Shift','Total Recon Rows','Match %'],
                                  'Value':[company,month,total_pos,total_bank,diff,matched,total,match_pct]})
            summary.to_excel(w,'Dashboard',index=False)
            recon.to_excel(w,'Detailed_Recon',index=False)
            date_summary.to_excel(w,'Date_Summary',index=False)
            store_summary.to_excel(w,'Store_Summary',index=False)
            ds.to_excel(w,'Date_Store_Summary',index=False)
            recon[~recon.status.isin(['Matched','Date Shift Match'])].to_excel(w,'Exceptions',index=False)
            bank_sett.to_excel(w,'Bank_Settlements',index=False)
            pos_clean.to_excel(w,'POS_Normalized',index=False)
            if not tm.empty: tm.to_excel(w,'Terminal_Master',index=False)
        return bio.getvalue()
    st.download_button('Download Final Monthly Excel Report',make_excel(),file_name=f'SettlementRecon_{month}.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',use_container_width=True)
