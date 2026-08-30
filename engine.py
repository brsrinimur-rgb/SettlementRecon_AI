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


# v1.1: explicit POS-scheme-code -> scheme_group mapping. Anything with a "POS <code>_"
# narration prefix that ISN'T in this map is no longer silently folded into CC --
# it is routed to the parser audit instead, so a new/unknown scheme can never
# silently contaminate an existing scheme's totals the way GC previously did.
SCHEME_CODE_MAP = {
    'MD': 'MADA',
    'CC': 'CC',       # bank does not split Visa/Mastercard at settlement level;
                       # card_scheme (below) keeps that detail for audit/reporting.
    'GC': 'GCC',
}


class MasterFileError(ValueError):
    """Raised when an uploaded file doesn't match the master file schema expected."""
    pass


def parse_bank_excel(file_bytes: bytes, filename: str='') -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (credits, audit).
    credits: one row per settlement batch, scheme-correct (see SCHEME_CODE_MAP).
    audit:   every bank statement line that was NOT included in credits, with a
             reason -- so nothing is silently discarded. Reviewable in the
             'Parser Audit' tab."""
    raw=pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=None)
    hdr=None
    for i in range(min(30,len(raw))):
        if any(str(v).strip()==BANK_HEADER for v in raw.iloc[i].tolist()): hdr=i; break
    if hdr is None: raise ValueError('Could not find ANB transaction header.')
    df=pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=hdr)
    df.columns=[str(c).strip() for c in df.columns]

    rows=[]; audit=[]

    def _audit(r, n1, n2, n3, reason):
        audit.append({
            'source_file': filename,
            'bank_posting_date': _date(r.get('Trans: Date')),
            'bank_tx_id': _clean_id(r.get('Txt ID')),
            'amount_dr': _amount(r.get('Amount Dr.', 0)),
            'amount_cr': _amount(r.get('Amount Cr.', 0)),
            'narration_1': n1, 'narration_2': n2, 'narration_3': n3,
            'reason': reason,
        })

    for _,r in df.iterrows():
        n1=str(r.get('Narration 1','') or '')
        n2=str(r.get('Narration 2','') or '')
        n3=str(r.get('Narration 3','') or '')

        if not n1.startswith('POS '):
            if n1.strip() and n1 != 'nan':
                reason = 'Amex direct settlement (no POS-detail source configured yet)' if 'amex' in n1.lower() \
                    else 'Non-POS bank activity (transfer / SAMA ref / other)'
                _audit(r, n1, n2, n3, reason)
            continue

        code_match = re.match(r'^POS\s+([A-Z]{2,3})[_ ]', n1)
        scheme_code = code_match.group(1) if code_match else None
        if scheme_code not in SCHEME_CODE_MAP:
            _audit(r, n1, n2, n3, f'Unrecognized POS scheme code "{scheme_code}" -- add to SCHEME_CODE_MAP once confirmed')
            continue
        scheme = SCHEME_CODE_MAP[scheme_code]

        m2=re.search(r'(\d+)_([0-9]+)_(\d{6})',n2)
        if not m2:
            _audit(r, n1, n2, n3, 'Could not parse Retailer ID / Terminal ID / Date from Narration 2')
            continue
        retailer, terminal, ddmmyy=m2.groups()
        try: settle_date=datetime.strptime(ddmmyy,'%d%m%y').date()
        except: settle_date=None

        kind='CREDIT'
        if '_VAT_' in n1: kind='VAT'
        elif '_FEE_' in n1 or '_MR_FEE_' in n1: kind='FEE'

        card=''
        if n3.startswith('VC_'): card='VISA'
        elif n3.startswith('MC_'): card='MASTERCARD'
        elif n3.lower().startswith('mada'): card='MADA'
        elif n3.upper().startswith('GCC'): card='GCC'

        amount_cr=_amount(r.get('Amount Cr.',0)); amount_dr=abs(_amount(r.get('Amount Dr.',0)))
        gross=fee=vat=0.0; tx=0
        if kind=='CREDIT':
            gross=amount_cr
            # n3 examples Mada_29.23_194.89_TX_4 or VC_2.58_17.18_TX_1 or GCC_4.63_30.86_TX_1
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

    audit_df = pd.DataFrame(audit)
    d=pd.DataFrame(rows)
    if d.empty: return d, audit_df
    # Combine credit/fee/vat rows into settlement-level records. Credit rows already carry fee/vat from narration; use linked rows as fallback/audit.
    credits=d[d.entry_type=='CREDIT'].copy()
    fees=d[d.entry_type=='FEE'].groupby(['retailer_id','terminal_id','settlement_date','scheme_group'],dropna=False)['debit'].sum().rename('fee_rows')
    vats=d[d.entry_type=='VAT'].groupby(['retailer_id','terminal_id','settlement_date','scheme_group'],dropna=False)['debit'].sum().rename('vat_rows')
    credits=credits.merge(fees,on=['retailer_id','terminal_id','settlement_date','scheme_group'],how='left').merge(vats,on=['retailer_id','terminal_id','settlement_date','scheme_group'],how='left')
    credits['fee']=credits['fee'].where(credits['fee']>0, credits['fee_rows'].fillna(0))
    credits['vat']=credits['vat'].where(credits['vat']>0, credits['vat_rows'].fillna(0))
    credits['net_settlement']=credits['gross_credit']-credits['fee']-credits['vat']
    return credits, audit_df


REQUIRED_MASTER_COLUMNS = {'Terminal ID', 'Store Code', 'Store Name'}


def parse_terminal_master(file_bytes: bytes) -> pd.DataFrame:
    """Raises MasterFileError with a clear message (instead of a raw KeyError)
    if the uploaded file isn't actually a Terminal ID Master -- e.g. someone
    uploads Merchant_ID.xlsx or Store_Mapping_FINAL.xlsx into this slot."""
    try:
        df=pd.read_excel(io.BytesIO(file_bytes),sheet_name=0)
    except Exception as e:
        raise MasterFileError(f'Could not read this file as Excel: {e}')
    df.columns=[str(c).strip() for c in df.columns]
    missing = REQUIRED_MASTER_COLUMNS - set(df.columns)
    if missing:
        raise MasterFileError(
            'This is not a Terminal ID Master. Expected columns '
            f'{sorted(REQUIRED_MASTER_COLUMNS)}, but this file has {list(df.columns)}. '
            'If this is the Merchant ID or Store Mapping file, upload it in the correct '
            'slot once that mapping is supported -- for now the Terminal ID Master is '
            'the only file used for store lookups.'
        )
    out=pd.DataFrame({
        'terminal_id':df['Terminal ID'].map(_clean_id),
        'store_code':df['Store Code'].map(_clean_id),
        'store_name':df['Store Name'].astype(str).str.strip(),
    })
    return out.drop_duplicates('terminal_id')


# v1.1: default widened 3 -> 10 days (real ANB settlement lag observed up to 9 days
# in production data), configurable 0-15 in the UI. A date-shifted match is still
# always labelled distinctly (never silently promoted to plain "Matched") -- see
# the status assignment below.
DEFAULT_MAX_DATE_SHIFT = 10
MAX_ALLOWED_DATE_SHIFT = 15


def reconcile(pos:pd.DataFrame, bank:pd.DataFrame, terminal_master:pd.DataFrame|None=None, tolerance:float=1.0, max_date_shift:int=DEFAULT_MAX_DATE_SHIFT):
    if pos.empty: raise ValueError('No POS transactions parsed.')
    if bank.empty: raise ValueError('No bank POS settlement credits parsed.')
    max_date_shift = max(0, min(max_date_shift, MAX_ALLOWED_DATE_SHIFT))
    pos=pos.drop_duplicates('dedupe_key').copy()
    # Daily terminal/scheme totals from transaction date
    pagg=pos.groupby(['transaction_date','retailer_id','terminal_id','scheme_group'],as_index=False).agg(
        pos_gross=('net_pos_amount','sum'), pos_tx=('transaction_count','sum'))
    if terminal_master is not None and not terminal_master.empty:
        pagg=pagg.merge(terminal_master,on='terminal_id',how='left')
        bank=bank.merge(terminal_master,on='terminal_id',how='left')
    else:
        pagg['store_code']='';pagg['store_name']='';bank['store_code']='';bank['store_name']=''
    # v1.1: never fabricate a store name. A terminal absent from the master gets an
    # explicit review flag instead of a blank -- surfaced as its own tab in app.py.
    pagg['store_name']=pagg['store_name'].where(pagg['store_name'].notna() & (pagg['store_name']!=''), None)
    bank['store_name']=bank['store_name'].where(bank['store_name'].notna() & (bank['store_name']!=''), None)
    mapping_review = sorted(set(pagg.loc[pagg['store_name'].isna(),'terminal_id']) |
                             set(bank.loc[bank['store_name'].isna(),'terminal_id']))
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
            elif abs(diff)<=tolerance: st='Late Settlement / Date Shift Match'
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
        recon['store_name']=recon['store_name'].replace('', None)
        recon.loc[recon['store_name'].isna() & recon['terminal_id'].isin(mapping_review), 'store_name'] = \
            recon.loc[recon['store_name'].isna() & recon['terminal_id'].isin(mapping_review), 'terminal_id'] \
                .apply(lambda t: f'(Unmapped -- Terminal {t}, needs review)')
    return pos,pagg,bank,recon,mapping_review
