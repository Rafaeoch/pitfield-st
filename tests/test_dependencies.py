"""Every third-party import must be declared in requirements.txt.

This exists because `requests` was imported by pipeline/ingest/sources.py --
the base of all ingestion -- while appearing in none of the three hand-written
install lists. It passed every test and every local run, because it had drifted
into the development venv as somebody's transitive dependency. It failed on the
first clean Linux box, at the first import, after the deploy had already built
a venv and downloaded a 31MB ephemeris kernel.

A missing dependency is invisible on the machine that has it. The only way to
see it is to compare what the source imports against what the project declares.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Import name -> distribution name, where they differ.
DISTRIBUTION_NAMES = {
    "yaml": "pyyaml",
    "pandas_market_calendars": "pandas-market-calendars",
}

# Imported for its side effect on another library rather than by name: polars
# reads and writes Parquet through pyarrow without importing it here.
INDIRECT = {"pyarrow"}


def _declared() -> set[str]:
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    return {
        line.strip().lower()
        for line in lines
        if line.strip() and not line.startswith("#")
    }


def _imported() -> dict[str, set[str]]:
    """Top-level third-party modules imported anywhere in the project."""
    out: dict[str, set[str]] = {}
    for path in [*ROOT.glob("pipeline/**/*.py"), *ROOT.glob("tests/**/*.py")]:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative: our own code
                    continue
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if not name or name in sys.stdlib_module_names or name == "pipeline":
                    continue
                out.setdefault(name, set()).add(str(path.relative_to(ROOT)))
    return out


def test_every_third_party_import_is_declared() -> None:
    declared = _declared()
    missing = {
        module: sorted(files)
        for module, files in _imported().items()
        if DISTRIBUTION_NAMES.get(module, module).lower() not in declared
    }
    assert not missing, (
        "imported but not in requirements.txt: "
        + "; ".join(f"{m} (in {', '.join(f)})" for m, f in sorted(missing.items()))
    )


def test_declared_dependencies_are_actually_used() -> None:
    """The reverse: nothing declared that nothing imports.

    Keeps the list from accumulating packages that were needed once, which is
    how a 40MB install becomes a 400MB one nobody dares to prune.
    """
    imported = {
        DISTRIBUTION_NAMES.get(m, m).lower() for m in _imported()
    } | INDIRECT
    unused = _declared() - imported
    assert not unused, f"declared but never imported: {sorted(unused)}"


@pytest.mark.parametrize("module", sorted(_imported()))
def test_import_is_installed(module: str) -> None:
    """And that the declared packages are importable in this environment."""
    if module == "anthropic":
        pytest.skip("optional; imported lazily inside summarize()")
    __import__(module)
