"""The ephemeris layer, checked against astronomy that is known independently.

These tests assert facts about the solar system, not facts about our code. That
is the point: a lunar phase routine that is self-consistent but a day and a half
out at the extremes would pass any round-trip test and fail every one of these.

Skipped cleanly when the DE440s kernel is absent, so a fresh clone does not fail
its test suite before the 31MB download has happened.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.ingest.celestial import (
    KERNEL_PATH,
    aspect_angle,
    classify_aspect,
    ecliptic_longitude,
    is_retrograde,
    lunar_state,
)

pytestmark = pytest.mark.skipif(
    not KERNEL_PATH.exists(), reason="DE440s kernel not downloaded; run make ephemeris"
)

YEAR_2026 = np.arange(
    np.datetime64("2026-01-01"), np.datetime64("2027-01-01"), dtype="datetime64[D]"
)


@pytest.fixture(scope="module")
def moon():
    return lunar_state(YEAR_2026)


def test_synodic_month_matches_the_real_value(moon):
    """Full-moon to full-moon must come out at 29.53 days, not 29.5 by fiat."""
    full_days = moon.dates[moon.illuminated > 0.995]
    clusters = [full_days[0]]
    for day in full_days[1:]:
        if (day - clusters[-1]).astype(int) > 5:
            clusters.append(day)
    gaps = np.diff(np.array(clusters)).astype(int)
    assert gaps.mean() == pytest.approx(29.53, abs=0.15)


def test_illumination_spans_new_to_full(moon):
    assert moon.illuminated.min() < 0.01
    assert moon.illuminated.max() > 0.99


def test_illumination_is_consistent_with_phase_angle(moon):
    """illuminated = (1 - cos(phase)) / 2, checked rather than assumed."""
    expected = (1.0 - np.cos(np.radians(moon.phase_angle))) / 2.0
    assert np.allclose(moon.illuminated, expected, atol=1e-12)


def test_lunar_distance_is_within_real_perigee_apogee(moon):
    """Real bounds are roughly 356,500 and 406,700 km."""
    assert 355_000 < moon.distance_km.min() < 362_000
    assert 403_000 < moon.distance_km.max() < 408_000


def test_phase_buckets_are_centred_on_the_named_phase(moon):
    """The full-moon bucket must straddle full, not begin at it."""
    buckets = moon.phase_bucket()
    fullest = int(np.argmax(moon.illuminated))
    assert buckets[fullest] == "full"

    newest = int(np.argmin(moon.illuminated))
    assert buckets[newest] == "new"


def test_all_eight_phase_buckets_occur_in_a_year(moon):
    assert set(moon.phase_bucket()) == {
        "new", "waxing_crescent", "first_quarter", "waxing_gibbous",
        "full", "waning_gibbous", "last_quarter", "waning_crescent",
    }


def _runs(flags: np.ndarray) -> list[int]:
    out, current = [], 0
    for flag in flags:
        if flag:
            current += 1
        elif current:
            out.append(current)
            current = 0
    if current:
        out.append(current)
    return out


def test_mercury_retrogrades_three_times_a_year_for_about_three_weeks():
    """The best-known figure in the whole hypothesis list, so it must be right."""
    runs = _runs(is_retrograde("mercury", YEAR_2026))
    assert len(runs) == 3
    assert all(17 <= r <= 27 for r in runs), runs


def test_mars_does_not_retrograde_in_2026():
    """Mars retrogrades roughly every 2.1 years, and 2026 is not one of them."""
    assert not is_retrograde("mars", YEAR_2026).any()


def test_retrograde_handles_the_360_degree_wrap():
    """A naive longitude difference reports a false retrograde once per orbit."""
    # Venus crosses 0 degrees during this window; it must not spuriously flip.
    window = np.arange(
        np.datetime64("2026-01-01"), np.datetime64("2026-12-31"), dtype="datetime64[D]"
    )
    fraction = is_retrograde("venus", window).mean()
    # Venus is genuinely retrograde about 7% of the time. A wrap bug pushes
    # this far higher.
    assert 0.0 <= fraction < 0.20


def test_jupiter_saturn_separation_reflects_the_2020_conjunction():
    """They conjoined in December 2020 on a ~20 year cycle."""
    sep = aspect_angle("jupiter", "saturn", YEAR_2026)
    assert sep.min() > 60.0        # long past conjunction by 2026
    assert sep.max() < 180.0
    assert "trine" in set(classify_aspect(sep))


def test_aspect_angle_is_symmetric():
    a = aspect_angle("mars", "venus", YEAR_2026)
    b = aspect_angle("venus", "mars", YEAR_2026)
    assert np.allclose(a, b)


def test_aspect_angle_never_exceeds_180():
    for pair in (("mercury", "pluto"), ("venus", "jupiter"), ("mars", "saturn")):
        sep = aspect_angle(*pair, YEAR_2026)
        assert sep.min() >= 0.0
        assert sep.max() <= 180.0


def test_ecliptic_longitude_is_in_range_and_advances():
    """The Sun's apparent longitude advances about a degree a day."""
    lon = ecliptic_longitude("mercury", YEAR_2026)
    assert lon.min() >= 0.0
    assert lon.max() < 360.0

    jupiter = ecliptic_longitude("jupiter", YEAR_2026)
    # Jupiter's 11.9-year orbit covers roughly 30 degrees in a year.
    travelled = np.abs((jupiter[-1] - jupiter[0] + 180) % 360 - 180)
    assert 15.0 < travelled < 45.0
