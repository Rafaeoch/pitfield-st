"""Chain capture: the point-in-time write rules, checked without a network.

These test the archive's discipline, not Alpaca. A correction must land beside
the original rather than replacing it, and a scheduled job that fires eight
times an afternoon must not manufacture eight corrections a day.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl
import pytest

from pipeline.ingest.capture import already_captured, partition_path, write_chain


def _rows(n: int = 3, source: str = "ALPACA") -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {
            "snapshot_ts": now,
            "quote_ts": now,
            "underlying": "SPY",
            "expiry": date(2026, 12, 18),
            "strike": 600.0 + i,
            "right": "C",
            "bid": 1.0 + i,
            "ask": 1.1 + i,
            "last": 1.05 + i,
            "volume": 10,
            "open_interest": None,
            "oi_effective_date": None,
            "multiplier": 100,
            "is_adjusted": False,
            "spot": 772.0,
            "source": source,
            "feed": "indicative",
            "ingest_version": "test",
            "occ_symbol": f"SPY261218C{int((600 + i) * 1000):08d}",
        }
        for i in range(n)
    ]


def test_writes_a_partition_in_the_documented_layout(tmp_path):
    path = write_chain(_rows(), date(2026, 9, 3), "SPY", tmp_path)
    assert path.parent == tmp_path / "date=2026-09-03" / "underlying=SPY"
    assert path.name == "part.parquet"
    assert len(pl.read_parquet(path)) == 3


def test_a_second_write_lands_beside_the_first_not_over_it(tmp_path):
    """Never overwrite. A correction is a new file with a later timestamp."""
    first = write_chain(_rows(3), date(2026, 9, 3), "SPY", tmp_path)
    second = write_chain(_rows(5), date(2026, 9, 3), "SPY", tmp_path)

    assert first.exists(), "the original must survive a correction"
    assert second != first
    assert second.name.startswith("part.correction-")
    assert len(pl.read_parquet(first)) == 3
    assert len(pl.read_parquet(second)) == 5


def test_already_captured_detects_an_archived_session(tmp_path):
    """What stops an hourly schedule writing a correction every hour.

    The market-calendar gate only answers "has the close passed", which stays
    true for the rest of the day. Without this check every later firing would
    archive the same session again.
    """
    assert not already_captured(date(2026, 9, 3), "SPY", tmp_path)
    write_chain(_rows(), date(2026, 9, 3), "SPY", tmp_path)
    assert already_captured(date(2026, 9, 3), "SPY", tmp_path)
    # A different session, and a different underlying, are untouched.
    assert not already_captured(date(2026, 9, 4), "SPY", tmp_path)
    assert not already_captured(date(2026, 9, 3), "QQQ", tmp_path)


def test_refuses_to_write_an_empty_chain(tmp_path):
    """An empty capture is a failure to record, not a session with no options."""
    with pytest.raises(ValueError):
        write_chain([], date(2026, 9, 3), "SPY", tmp_path)


def test_unknown_open_interest_survives_the_round_trip(tmp_path):
    """None must not become 0 on the way to disk and back.

    Unknown open interest and zero open interest are different facts, and every
    gamma-exposure number depends on telling them apart.
    """
    path = write_chain(_rows(), date(2026, 9, 3), "SPY", tmp_path)
    back = pl.read_parquet(path)
    assert back["open_interest"].null_count() == len(back)


def test_the_feed_is_recorded_on_every_row(tmp_path):
    """A surface must never be ambiguous about the quotes it was fitted to."""
    path = write_chain(_rows(), date(2026, 9, 3), "SPY", tmp_path)
    back = pl.read_parquet(path)
    assert set(back["feed"].to_list()) == {"indicative"}
    assert set(back["source"].to_list()) == {"ALPACA"}
