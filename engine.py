from __future__ import annotations
import io, re, hashlib
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Iterable
import pandas as pd

BANK_HEADER = 'Trans: Date'


def _clean_id(v):
    if pd.isna(v): return ''
    s = str(v).strip()
    if s.endswith('.0'): s = s[:-2]
    return s

def _date(v):
    try:
        x = pd.to_datetime(v, errors='coerce')
        return None if pd.isna(x) else x.date()
    except Exception:
        return None

def _amount(v):
    try:
        if pd.isna(v): return 0.0
        return float(str(v).replace(',','').strip())
    except Exception:
        return 0.0


def parse_pos_excel(file_bytes: bytes, filename: str='') -> pd.DataFrame:
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    out=[]
    for sheet in xls.sheet_names:
        sn = sheet.strip().lower()
        if 'details_mada' not in sn and 'details_cc' not in sn:
            continue
        raw = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet, header=None)
        hdr = None
        for i in range(min(15, len(raw))):
            vals=[str(x).strip() for x in raw.iloc[i].tolist()]
            if 'Merchant ID' in vals and 'Terminal ID' in vals and 'Transaction Amount' in vals:
                hdr=i; break
        if hdr is None: continue
        df=pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet, header=hdr)
        df.columns=[str(c).strip() for c in df.columns]
        df=df.loc[:, ~df.columns.str.startswith('Unnamed')]
        scheme='MADA' if 'mada' in sn else 'CC'
        rows=pd.DataFrame({
            'source_file': filename,
            'scheme_group': scheme,
            'merchant_id': df.get('Merchant ID','').map(_clean_id) if 'Merchant ID' in df else '',
            'retailer_id': df.get('Retailer Id','').map(_clean_id) if 'Retailer Id' in df else '',
            'transaction_date': df.get('Transaction Date','').map(_date) if 'Transaction Date' in df else None,
            'posting_date': df.get('Posting Date','').map(_date) if 'Posting Date' in df else None,
            'terminal_id': df.get('Terminal ID','').map(_clean_id) if 'Terminal ID' in df else '',
            'transaction_count': pd.to_numeric(df.get('Num Of Transactions',1), errors='coerce').fillna(1),
            'gross_amount': pd.to_numeric(df.get('Transaction Amount',0), errors='coerce').fillna(0.0),
            'reversal_count': pd.to_numeric(df.get('Num Of Reversal Transaction',0), errors='coerce').fillna(0),
            'reversal_amount': pd.to_numeric(df.get('Reversal Transaction Amount',0), errors='coerce').fillna(0.0) if 'Reversal Transaction Amount' in df else 0.0,
        })
        rows=rows[(rows['terminal_id']!='') & rows['transaction_date'].notna()]
        rows['net_pos_amount']=rows['gross_amount']-rows['reversal_amount']
        rows['dedupe_key']=rows.apply(lambda r: hashlib.sha1('|'.join(map(str,[r.retailer_id,r.terminal_id,r.transaction_date,r.get('posting_date'),round(r.gross_amount,2),r.scheme_group])).encode()).hexdigest(),axis=1)
        out.append(rows)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def parse_bank_excel(file_bytes: bytes, filename: str='') -> pd.DataFrame:
    raw=pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=None)
    hdr=None
    for i in range(min(30,len(raw))):
        if any(str(v).strip()==BANK_HEADER for v in raw.iloc[i].tolist()): hdr=i; break
    if hdr is None: raise ValueError('Could not find ANB transaction header.')
    df=pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=hdr)
    df.columns=[str(c).strip() for c in df.columns]
    rows=[]
    for _,r in df.iterrows():
        n1=str(r.get('Narration 1','') or '')
        n2=str(r.get('Narration 2','') or '')
        n3=str(r.get('Narration 3','') or '')
        if not n1.startswith('POS '): continue
        m2=re.search(r'(\d+)_([0-9]+)_(\d{6})',n2)
        if not m2: continue
        retailer, terminal, ddmmyy=m2.groups()
        try: settle_date=datetime.strptime(ddmmyy,'%d%m%y').date()
        except: settle_date=None
        kind='CREDIT'
        if '_VAT_' in n1: kind='VAT'
        elif '_FEE_' in n1 or '_MR_FEE_' in n1: kind='FEE'
        scheme='MADA' if ' MD_' in n1 or n3.lower().startswith('mada') else 'CC'
        card=''
        if n3.startswith('VC_'): card='VISA'
        elif n3.startswith('MC_'): card='MASTERCARD'
        elif n3.lower().startswith('mada'): card='MADA'
        amount_cr=_amount(r.get('Amount Cr.',0)); amount_dr=abs(_amount(r.get('Amount Dr.',0)))
        gross=fee=vat=0.0; tx=0
        if kind=='CREDIT':
            gross=amount_cr
            # n3 examples Mada_29.23_194.89_TX_4 or VC_2.58_17.18_TX_1
            p=n3.split('_')
            try:
                if len(p)>=5:
                    vat=float(p[1]); fee=float(p[2]); tx=int(float(p[-1]))
            except: pass
        elif kind=='FEE': fee=amount_dr
        elif kind=='VAT': vat=amount_dr
        rows.append({
            'source_file': filename,
            'bank_posting_date': _date(r.get('Trans: Date')),
            'value_date': _date(r.get('Value Date')),
            'bank_tx_id': _clean_id(r.get('Txt ID')),
            'settlement_date': settle_date,
            'retailer_id': retailer,
            'terminal_id': terminal,
            'scheme_group': scheme,
            'card_scheme': card,
            'entry_type': kind,
            'gross_credit': gross,
            'fee': fee,
            'vat': vat,
            'transaction_count': tx,
            'debit': amount_dr,
            'credit': amount_cr,
            'narration_1': n1,'narration_2':n2,'narration_3':n3,
        })
    d=pd.DataFrame(rows)
    if d.empty: return d
    # Combine credit/fee/vat rows into settlement-level records. Credit rows already carry fee/vat from narration; use linked rows as fallback/audit.
    credits=d[d.entry_type=='CREDIT'].copy()
    fees=d[d.entry_type=='FEE'].groupby(['retailer_id','terminal_id','settlement_date','scheme_group'],dropna=False)['debit'].sum().rename('fee_rows')
    vats=d[d.entry_type=='VAT'].groupby(['retailer_id','terminal_id','settlement_date','scheme_group'],dropna=False)['debit'].sum().rename('vat_rows')
    credits=credits.merge(fees,on=['retailer_id','terminal_id','settlement_date','scheme_group'],how='left').merge(vats,on=['retailer_id','terminal_id','settlement_date','scheme_group'],how='left')
    credits['fee']=credits['fee'].where(credits['fee']>0, credits['fee_rows'].fillna(0))
    credits['vat']=credits['vat'].where(credits['vat']>0, credits['vat_rows'].fillna(0))
    credits['net_settlement']=credits['gross_credit']-credits['fee']-credits['vat']
    return credits


def parse_terminal_master(file_bytes: bytes) -> pd.DataFrame:
    df=pd.read_excel(io.BytesIO(file_bytes),sheet_name=0)
    df.columns=[str(c).strip() for c in df.columns]
    out=pd.DataFrame({
        'terminal_id':df['Terminal ID'].map(_clean_id),
        'store_code':df['Store Code'].map(_clean_id),
        'store_name':df['Store Name'].astype(str).str.strip(),
    })
    return out.drop_duplicates('terminal_id')


def reconcile(pos:pd.DataFrame, bank:pd.DataFrame, terminal_master:pd.DataFrame|None=None, tolerance:float=1.0, max_date_shift:int=3):
    if pos.empty: raise ValueError('No POS transactions parsed.')
    if bank.empty: raise ValueError('No bank POS settlement credits parsed.')
    pos=pos.drop_duplicates('dedupe_key').copy()
    # Daily terminal/scheme totals from transaction date
    pagg=pos.groupby(['transaction_date','retailer_id','terminal_id','scheme_group'],as_index=False).agg(
        pos_gross=('net_pos_amount','sum'), pos_tx=('transaction_count','sum'))
    if terminal_master is not None and not terminal_master.empty:
        pagg=pagg.merge(terminal_master,on='terminal_id',how='left')
        bank=bank.merge(terminal_master,on='terminal_id',how='left')
    else:
        pagg['store_code']='';pagg['store_name']='';bank['store_code']='';bank['store_name']=''
    # Match each bank settlement to POS date primarily by embedded settlement date, with controlled date shift search.
    used=set(); rec=[]
    for bi,b in bank.reset_index(drop=True).iterrows():
        c=pagg[(pagg.retailer_id==b.retailer_id)&(pagg.terminal_id==b.terminal_id)&(pagg.scheme_group==b.scheme_group)].copy()
        if c.empty:
            best=None
        else:
            c['date_shift']=c.transaction_date.map(lambda d: abs((d-b.settlement_date).days) if d and b.settlement_date else 999)
            c['amt_diff']=(c.pos_gross-b.gross_credit).abs()
            c=c[c.date_shift<=max_date_shift]
            c=c[~c.index.isin(used)]
            best=None if c.empty else c.sort_values(['amt_diff','date_shift']).iloc[0]
        if best is None:
            rec.append({**b.to_dict(),'pos_date':None,'pos_gross':0.0,'pos_tx':0,'gross_difference':-b.gross_credit,'date_shift_days':None,'status':'Missing POS / Review'})
        else:
            used.add(best.name)
            diff=round(best.pos_gross-b.gross_credit,2)
            shift=(best.transaction_date-b.settlement_date).days if b.settlement_date else None
            if abs(diff)<=tolerance and (shift==0): st='Matched'
            elif abs(diff)<=tolerance: st='Date Shift Match'
            else: st='Amount Difference'
            r={**b.to_dict(),'pos_date':best.transaction_date,'pos_gross':best.pos_gross,'pos_tx':best.pos_tx,'gross_difference':diff,'date_shift_days':shift,'status':st}
            if not r.get('store_code'): r['store_code']=best.get('store_code','')
            if not r.get('store_name'): r['store_name']=best.get('store_name','')
            rec.append(r)
    # Unused POS aggregates
    for idx,p in pagg.iterrows():
        if idx in used: continue
        rec.append({'source_file':'','bank_posting_date':None,'value_date':None,'bank_tx_id':'','settlement_date':None,
                    'retailer_id':p.retailer_id,'terminal_id':p.terminal_id,'scheme_group':p.scheme_group,'card_scheme':'',
                    'gross_credit':0.0,'fee':0.0,'vat':0.0,'net_settlement':0.0,'transaction_count':0,
                    'store_code':p.get('store_code',''),'store_name':p.get('store_name',''),'pos_date':p.transaction_date,
                    'pos_gross':p.pos_gross,'pos_tx':p.pos_tx,'gross_difference':p.pos_gross,'date_shift_days':None,'status':'Missing Bank'})
    recon=pd.DataFrame(rec)
    if not recon.empty:
        for c in ['gross_credit','fee','vat','net_settlement','pos_gross','gross_difference']:
            recon[c]=pd.to_numeric(recon[c],errors='coerce').fillna(0.0)
    return pos,pagg,bank,recon
