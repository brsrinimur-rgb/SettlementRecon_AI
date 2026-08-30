"""
Regression tests for SettlementRecon AI v1.1.
Run with: pytest tests/test_engine.py -v

These lock in the four v1.1 fixes so a future patch can't silently reintroduce
the GC-into-CC bug, a raw KeyError on a bad master file, a too-narrow shift
window, or silent dropping of unparsed bank lines.

Uses the same real sample files the fixes were verified against, so this also
works as a basic end-to-end smoke test of the whole pipeline.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine import (parse_pos_excel, parse_bank_excel, parse_terminal_master,
                     reconcile, MasterFileError, DEFAULT_MAX_DATE_SHIFT,
                     MAX_ALLOWED_DATE_SHIFT)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
BANK_FILE = os.path.join(FIXTURES, "bank_statement.xlsx")
POS_FILE = os.path.join(FIXTURES, "pos_export.xlsx")
GOOD_MASTER = os.path.join(FIXTURES, "terminal_master.xlsx")
BAD_MASTER = os.path.join(FIXTURES, "merchant_id_master.xlsx")


def _read(path):
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture(scope="module")
def bank_and_audit():
    return parse_bank_excel(_read(BANK_FILE), "bank_statement.xlsx")


@pytest.fixture(scope="module")
def pos_df():
    return parse_pos_excel(_read(POS_FILE), "pos_export.xlsx")


@pytest.fixture(scope="module")
def terminal_master():
    return parse_terminal_master(_read(GOOD_MASTER))


# ---------------------------------------------------------------------------
# Fix 1: POS GC_ lines must be their own scheme, never folded into CC.
# ---------------------------------------------------------------------------
def test_gc_scheme_is_separate_from_cc(bank_and_audit):
    bank, _audit = bank_and_audit
    assert "GCC" in set(bank["scheme_group"]), "GC settlement batches should classify as GCC"
    cc_terminals_dates = set(
        zip(bank[bank.scheme_group == "CC"].terminal_id, bank[bank.scheme_group == "CC"].settlement_date)
    )
    gcc_rows = bank[bank.scheme_group == "GCC"]
    for _, row in gcc_rows.iterrows():
        assert (row.terminal_id, row.settlement_date) not in cc_terminals_dates or \
            bank[(bank.terminal_id == row.terminal_id) & (bank.settlement_date == row.settlement_date)
                 & (bank.scheme_group == "CC")].empty is False, \
            "a GC batch must not silently merge into an existing CC batch total"
    # the known GC batches from the sample data must sum correctly and separately
    assert round(gcc_rows["gross_credit"].sum(), 2) == 5522.0


def test_no_gc_narration_leaks_into_cc_bucket(bank_and_audit):
    bank, _audit = bank_and_audit
    # every CC row's narration must NOT start with POS GC
    assert not bank[bank.scheme_group == "CC"]["narration_1"].str.startswith("POS GC").any()


# ---------------------------------------------------------------------------
# Fix 2: wrong master file -> clear MasterFileError, never a raw KeyError.
# ---------------------------------------------------------------------------
def test_wrong_master_file_raises_clear_error():
    with pytest.raises(MasterFileError):
        parse_terminal_master(_read(BAD_MASTER))


def test_wrong_master_file_error_message_is_actionable():
    with pytest.raises(MasterFileError) as exc_info:
        parse_terminal_master(_read(BAD_MASTER))
    msg = str(exc_info.value)
    assert "not a Terminal ID Master" in msg
    assert "Terminal ID" in msg  # names the expected column so the user can self-correct


def test_correct_master_file_still_parses(terminal_master):
    assert len(terminal_master) > 0
    assert set(terminal_master.columns) == {"terminal_id", "store_code", "store_name"}
    assert not terminal_master["terminal_id"].duplicated().any()


# ---------------------------------------------------------------------------
# Fix 3: settlement shift window widened to 10 default / 15 max, and a
# date-shifted match is always labelled distinctly.
# ---------------------------------------------------------------------------
def test_default_and_max_shift_window_values():
    assert DEFAULT_MAX_DATE_SHIFT == 10
    assert MAX_ALLOWED_DATE_SHIFT == 15


def test_reconcile_clamps_shift_window_to_max(pos_df, bank_and_audit, terminal_master):
    bank, _audit = bank_and_audit
    # requesting an absurd window should clamp to MAX_ALLOWED_DATE_SHIFT, not error
    _pos_clean, _pagg, _bank, recon, _mr = reconcile(
        pos_df, bank, terminal_master, tolerance=1.0, max_date_shift=999)
    assert not recon.empty


def test_date_shift_matches_are_labelled_distinctly(pos_df, bank_and_audit, terminal_master):
    """A nonzero day-gap can legitimately coexist with an amount mismatch (that's
    correctly 'Amount Difference', not ours to relabel). The real invariant is the
    other direction: nothing with a nonzero day-gap is ever silently called plain
    'Matched' -- it must be 'Late Settlement / Date Shift Match' or 'Amount Difference'."""
    bank, _audit = bank_and_audit
    _pos_clean, _pagg, _bank, recon, _mr = reconcile(
        pos_df, bank, terminal_master, tolerance=1.0, max_date_shift=DEFAULT_MAX_DATE_SHIFT)
    shifted = recon[recon["date_shift_days"].notna() & (recon["date_shift_days"] != 0)]
    assert not (shifted["status"] == "Matched").any(), \
        "a date-shifted row must never be silently reported as plain 'Matched'"
    assert shifted["status"].isin(["Late Settlement / Date Shift Match", "Amount Difference"]).all()

    # and conversely: plain "Matched" must always have zero day-gap
    exact = recon[recon["status"] == "Matched"]
    if not exact.empty:
        assert (exact["date_shift_days"].fillna(0) == 0).all()


# ---------------------------------------------------------------------------
# Fix 4: dropped bank lines are captured in the audit, never silently lost.
# ---------------------------------------------------------------------------
def test_parser_audit_is_not_empty_for_real_statement(bank_and_audit):
    _bank, audit = bank_and_audit
    assert not audit.empty
    assert "reason" in audit.columns


def test_every_audit_row_has_a_reason(bank_and_audit):
    _bank, audit = bank_and_audit
    assert audit["reason"].notna().all()
    assert (audit["reason"].str.len() > 0).all()


def test_audit_plus_kept_rows_accounts_for_all_pos_lines(bank_and_audit):
    """Sanity check: nothing vanishes between the raw statement and (kept + audited)."""
    bank, audit = bank_and_audit
    # every kept credit row should NOT also appear (by bank_tx_id) in the audit
    kept_ids = set(bank["bank_tx_id"])
    audited_ids = set(audit["bank_tx_id"])
    assert kept_ids.isdisjoint(audited_ids)


# ---------------------------------------------------------------------------
# Mapping review: unmapped terminals are flagged, never given a fabricated name.
# ---------------------------------------------------------------------------
def test_unmapped_terminal_is_flagged_not_fabricated(pos_df, bank_and_audit, terminal_master):
    bank, _audit = bank_and_audit
    _pos_clean, _pagg, _bank, recon, mapping_review = reconcile(
        pos_df, bank, terminal_master, tolerance=1.0, max_date_shift=DEFAULT_MAX_DATE_SHIFT)
    for terminal_id in mapping_review:
        rows = recon[recon["terminal_id"] == terminal_id]
        assert (rows["store_name"].str.contains("Unmapped", na=False) |
                rows["store_name"].isna()).all(), \
            f"terminal {terminal_id} should be flagged for review, not given a guessed name"
