"""OCC option symbol parsing.

Every US listed option carries a 21-character OCC symbol that encodes the
underlying, the expiry, the right and the strike:

    SPY   260918 C 00600000
    root  YYMMDD  ^  strike x 1000, zero-padded to 8

Parsing it locally rather than asking the API for contract metadata saves an
enormous number of calls on a backfill — one chain is thousands of contracts,
and their expiry and strike are already sitting in the symbol.

The root is left-padded to six characters with spaces in the strict OCC form,
but every broker API returns it unpadded, so both are accepted.

Non-standard deliverables are the trap here. After a split or a special
dividend the OCC root gains a numeric suffix (SPY1, SPY2) and the contract
prices off a different deliverable. Those must never enter a surface fit, so
the parser flags them rather than quietly normalising the root.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# root (1-6 chars, letters then optional digit suffix), YYMMDD, C/P, 8-digit strike
# The root is normally letters, optionally with a trailing digit after a
# corporate action (SPY1). Some venues and vendors instead *prepend* the digit
# (1SPY), which real Alpaca data does — one contract in five thousand. Both
# forms mean the same thing: a non-standard deliverable.
_OCC = re.compile(
    r"^(?P<prefix>\d?)(?P<root>[A-Z]{1,5}\d?)(?P<ymd>\d{6})"
    r"(?P<right>[CP])(?P<strike>\d{8})$"
)

STRIKE_DIVISOR = 1000.0


@dataclass(frozen=True)
class OCCSymbol:
    symbol: str
    root: str
    underlying: str
    expiry: date
    right: str
    strike: float
    is_adjusted: bool

    @property
    def standard_multiplier(self) -> int:
        return 100


def parse(symbol: str) -> OCCSymbol:
    """Parse an OCC symbol. Raises ValueError on anything unrecognised.

    We raise rather than return None: a symbol we cannot parse is a contract we
    cannot place on a surface, and silently dropping it would understate the
    exclusion count that the quality page publishes.
    """
    raw = symbol.strip().replace(" ", "")
    match = _OCC.match(raw.upper())
    if not match:
        raise ValueError(f"not an OCC option symbol: {symbol!r}")

    root = match.group("root")
    prefix = match.group("prefix")
    ymd = match.group("ymd")
    year = 2000 + int(ymd[0:2])
    expiry = date(year, int(ymd[2:4]), int(ymd[4:6]))

    # A digit on either end of the root marks a non-standard deliverable.
    adjusted = bool(prefix) or bool(re.search(r"\d$", root))
    underlying = re.sub(r"\d$", "", root)

    return OCCSymbol(
        symbol=raw.upper(),
        root=prefix + root,
        underlying=underlying,
        expiry=expiry,
        right=match.group("right"),
        strike=int(match.group("strike")) / STRIKE_DIVISOR,
        is_adjusted=adjusted,
    )


def build(underlying: str, expiry: date, right: str, strike: float) -> str:
    """Inverse of :func:`parse`, for constructing symbols to request."""
    if right not in ("C", "P"):
        raise ValueError(f"right must be C or P, got {right!r}")
    strike_int = int(round(strike * STRIKE_DIVISOR))
    return f"{underlying.upper()}{expiry:%y%m%d}{right}{strike_int:08d}"
