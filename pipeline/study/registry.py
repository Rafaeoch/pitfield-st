"""Load and validate the frozen hypothesis registry."""

from __future__ import annotations

import importlib
from pathlib import Path

import yaml

REGISTRY_PATH = Path("pipeline/study/hypotheses.yaml")

# Hypotheses with no runnable variable module, and why. Listed explicitly and
# reported, rather than silently absent: a hypothesis that could not be tested
# is a fact about the study, not a gap to be tidied away.
NOT_IMPLEMENTED = {
    "bradley_siderograph": (
        "Bradley's published weights (1948) could not be sourced. The "
        "pre-registration forbids inventing them and requires any "
        "reconstruction be labelled as one, so this hypothesis is reported as "
        "not run rather than run against guessed coefficients."
    ),
}


def load(path: Path = REGISTRY_PATH) -> dict:
    spec = yaml.safe_load(path.read_text())
    defaults = spec.get("defaults", {})
    hypotheses = spec["hypotheses"]

    ids = [h["id"] for h in hypotheses]
    if len(ids) != len(set(ids)):
        dupes = {i for i in ids if ids.count(i) > 1}
        raise ValueError(f"duplicate hypothesis ids in registry: {sorted(dupes)}")

    for h in hypotheses:
        for field in ("id", "name", "family", "tier", "variable_module", "bucketing"):
            if field not in h:
                raise ValueError(f"hypothesis {h.get('id', '?')} is missing {field!r}")
        h.setdefault("return_series", defaults.get("return_series", []))

    return {"defaults": defaults, "hypotheses": hypotheses}


def variable_module(spec: dict):
    """Import the module named by a hypothesis, or None if not implemented."""
    if spec["id"] in NOT_IMPLEMENTED:
        return None
    name = spec["variable_module"].replace("variables.", "")
    return importlib.import_module(f"pipeline.study.variables.{name}")
