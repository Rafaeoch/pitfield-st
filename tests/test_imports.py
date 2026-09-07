"""Every module in the pipeline must import.

pipeline/ingest/probe.py imported TRADING_BASE from the Alpaca provider long
after that constant had become resolve_trading_base(), a function. The module
had been un-importable for some time and nothing noticed, because no test
imported it and the daily jobs do not call it. It surfaced only when someone
ran the probe by hand against a live server.

An import is the cheapest possible assertion and it catches a whole class of
refactor debris: renamed constants, moved helpers, deleted names.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import pipeline

MODULES = sorted(
    m.name
    for m in pkgutil.walk_packages(pipeline.__path__, prefix="pipeline.")
    if not m.ispkg
)


def test_the_walk_found_the_modules() -> None:
    """Guard against this file passing vacuously if discovery ever breaks."""
    assert len(MODULES) > 25, f"only found {len(MODULES)}: {MODULES}"


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    importlib.import_module(name)
