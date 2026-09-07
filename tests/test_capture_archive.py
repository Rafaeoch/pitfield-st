"""Selecting which captured file represents a session.

capture.py never overwrites. The first write of a session is part.parquet and
any later one lands beside it as part.correction-HHMMSS.parquet, so the archive
retains both what it originally published and what replaced it.

Two ways to get this wrong, and the first version of the loader managed both:

* Read every file, and one session is published several times. The observed
  result was n_days=3 over two real sessions, with dates
  ['2026-09-03', '2026-09-03', '2026-09-04'] -- internally consistent, wrong,
  and silent. IV rank and percentile were then computed against a distribution
  containing a duplicated day.
* Take the lexical maximum, and it is worse than arbitrary: "part.parquet"
  sorts AFTER "part.correction-...", so the obvious one-liner deterministically
  republishes the data a correction exists to supersede.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.run_day import _authoritative_partition


def _touch(directory: Path, *names: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(b"")


def test_uncorrected_session_uses_the_original(tmp_path: Path) -> None:
    _touch(tmp_path, "part.parquet")
    assert _authoritative_partition(tmp_path).name == "part.parquet"


def test_a_correction_supersedes_the_original(tmp_path: Path) -> None:
    _touch(tmp_path, "part.parquet", "part.correction-024858.parquet")
    assert _authoritative_partition(tmp_path).name == "part.correction-024858.parquet"


def test_the_newest_correction_wins(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "part.parquet",
        "part.correction-024858.parquet",
        "part.correction-131500.parquet",
        "part.correction-093000.parquet",
    )
    assert _authoritative_partition(tmp_path).name == "part.correction-131500.parquet"


def test_lexical_max_would_pick_the_superseded_file(tmp_path: Path) -> None:
    """The specific wrong answer this function exists to avoid."""
    _touch(tmp_path, "part.parquet", "part.correction-024858.parquet")
    naive = max(sorted(tmp_path.glob("*.parquet")))
    assert naive.name == "part.parquet", "premise of the regression has changed"
    assert _authoritative_partition(tmp_path) != naive


def test_empty_directory_yields_nothing(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    assert _authoritative_partition(tmp_path) is None


def test_one_session_yields_exactly_one_partition(tmp_path: Path) -> None:
    """Whatever a session holds, it contributes a single file to the archive."""
    for names in (
        ("part.parquet",),
        ("part.parquet", "part.correction-010101.parquet"),
        ("part.parquet", "part.correction-010101.parquet", "part.correction-020202.parquet"),
    ):
        directory = tmp_path / f"case{len(names)}"
        _touch(directory, *names)
        chosen = _authoritative_partition(directory)
        assert chosen is not None and chosen.exists()
