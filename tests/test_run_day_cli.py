"""The archive builder must actually run end to end.

pipeline/run_day.py:695 referenced `rows`, a name that only exists inside
compute_day(). Every unit test passed and every module imported; the failure
appeared only when main() was invoked, which no test did. It surfaced on a
server, mid-deploy, after six minutes of unrelated work.

This runs the real entry point over a short archive and asserts the published
shape. It is slower than a unit test and worth it: this function is what the
scheduled job calls.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_main_builds_a_publishable_archive(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pipeline.run_day", "--days", "3", "--out", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr[-2000:]

    index = json.loads((tmp_path / "index.json").read_text())
    for key in ("generated_at", "pipeline_version", "synthetic", "underlying",
                "latest_date", "n_days", "dates", "contract_days", "history"):
        assert key in index, f"index.json missing {key}"

    assert index["n_days"] == 3
    assert len(index["dates"]) == 3
    assert index["synthetic"] is True, "a synthetic build must say so"
    assert (tmp_path / "latest.json").exists()
    assert len(list((tmp_path / "archive").glob("*.json"))) == 3
