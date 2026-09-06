"""OCC symbol parsing, including the adjusted-contract case that wrecks fits."""

from __future__ import annotations

from datetime import date

import pytest

from pipeline.ingest.providers.occ import build, parse


def test_parses_a_standard_call():
    s = parse("SPY260918C00600000")
    assert s.underlying == "SPY"
    assert s.expiry == date(2026, 9, 18)
    assert s.right == "C"
    assert s.strike == 600.0
    assert not s.is_adjusted


def test_parses_a_put_with_a_fractional_strike():
    s = parse("QQQ261120P00457500")
    assert s.right == "P"
    assert s.strike == 457.5
    assert s.expiry == date(2026, 11, 20)


def test_flags_adjusted_contracts():
    """A numeric root suffix means a non-standard deliverable.

    These price off something other than 100 shares and will silently wreck a
    calibration, so they must be flagged, not normalised away.
    """
    s = parse("SPY1260918C00600000")
    assert s.is_adjusted
    assert s.underlying == "SPY"
    assert s.root == "SPY1"


def test_handles_padded_and_lowercase_forms():
    assert parse("spy260918c00600000").strike == 600.0
    assert parse(" SPY260918C00600000 ").underlying == "SPY"


@pytest.mark.parametrize("bad", ["", "SPY", "SPY260918X00600000", "SPY26091C00600000", "NOTASYMBOL"])
def test_rejects_unparseable_symbols(bad):
    """Raise rather than return None: a dropped contract must be counted."""
    with pytest.raises(ValueError):
        parse(bad)


def test_round_trips():
    for underlying, expiry, right, strike in [
        ("SPY", date(2026, 9, 18), "C", 600.0),
        ("IWM", date(2027, 1, 15), "P", 187.5),
        ("AAPL", date(2026, 12, 18), "C", 232.5),
    ]:
        symbol = build(underlying, expiry, right, strike)
        parsed = parse(symbol)
        assert parsed.underlying == underlying
        assert parsed.expiry == expiry
        assert parsed.right == right
        assert parsed.strike == pytest.approx(strike)


def test_strike_encoding_is_thousandths():
    assert build("SPY", date(2026, 9, 18), "C", 600.0).endswith("00600000")
    assert build("SPY", date(2026, 9, 18), "C", 0.5).endswith("00000500")


def test_parses_a_prefixed_adjusted_contract():
    """Real Alpaca data prepends the adjustment digit: 1SPY, not SPY1.

    Seen once in roughly five thousand SPY contracts. Both spellings mean a
    non-standard deliverable and must be flagged, never silently normalised
    into the standard chain.
    """
    s = parse("1SPY250117P00370010")
    assert s.underlying == "SPY"
    assert s.root == "1SPY"
    assert s.is_adjusted
    assert s.strike == pytest.approx(370.010)
    assert s.right == "P"
    assert s.expiry == date(2025, 1, 17)


def test_standard_contracts_are_still_not_flagged():
    assert not parse("SPY250117P00590000").is_adjusted


def test_contract_metadata_requests_the_whole_chain():
    """Guards a bug that silently produced wrong gamma figures.

    Alpaca's /v2/options/contracts returns only the nearest expiry when no
    expiration filter is supplied — 852 contracts of a 12,534-contract SPY
    chain — and returns it with a 200 and no next_page_token, so it looks
    complete. Open interest then covers 4% of rows instead of 76%, and every
    gamma number is computed from almost nothing while appearing to work.

    This asserts the filter is present in the source rather than mocking the
    API: the failure mode is an omitted parameter, so the parameter is the
    thing worth pinning.
    """
    from pathlib import Path

    source = Path("pipeline/ingest/providers/alpaca.py").read_text()
    start = source.index("def fetch_contract_metadata")
    body = source[start : source.index("def ", start + 10)]
    assert "expiration_date_gte" in body, (
        "fetch_contract_metadata must bound the expiry window, or it returns "
        "only the front expiry"
    )
