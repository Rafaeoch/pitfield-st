"""Angular separations between planet pairs, and the classical aspects."""
from __future__ import annotations
import numpy as np
from ...ingest.celestial import DEFAULT_ORB, aspect_angle, classify_aspect
from .base import binary, result

PAIR_FOR = {
    "jupiter_saturn_conjunction": ("jupiter", "saturn"),
    "saturn_uranus_aspect": ("saturn", "uranus"),
    "saturn_pluto_aspect": ("saturn", "pluto"),
}

def compute(dates, spec=None) -> dict:
    hid = spec["id"]
    if hid == "planetary_aspects_generic":
        # Every pair at once: a day is labelled by whichever aspect is exact,
        # or "none". With eight bodies that is 28 pairs, and the great majority
        # of days carry no aspect within orb at all.
        bodies = ["mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "pluto"]
        label = np.full(len(dates), "none", dtype=object)
        tightest = np.full(len(dates), np.inf)
        for i in range(len(bodies)):
            for j in range(i + 1, len(bodies)):
                sep = aspect_angle(bodies[i], bodies[j], dates)
                named = classify_aspect(sep, DEFAULT_ORB)
                for exact, name in ((0.0, "conjunction"), (60.0, "sextile"),
                                    (90.0, "square"), (120.0, "trine"),
                                    (180.0, "opposition")):
                    hit = named == name
                    closeness = np.abs(sep - exact)
                    better = hit & (closeness < tightest)
                    label[better] = name
                    tightest[better] = closeness[better]
        return result(dates, np.where(np.isfinite(tightest), tightest, np.nan),
                      label.astype(str))

    a, b = PAIR_FOR[hid]
    sep = aspect_angle(a, b, dates)
    if hid == "jupiter_saturn_conjunction":
        flags = sep <= 10.0          # a wide conjunction window; ~20-year cycle
        return result(dates, sep, binary(flags, "conjunct", "apart"))
    named = classify_aspect(sep, DEFAULT_ORB)
    return result(dates, sep, binary(named != "none", "aspect", "none"))
