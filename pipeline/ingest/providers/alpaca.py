"""Alpaca options and equity data.

Credentials come from the environment and are never written to disk or logged:

    APCA_API_KEY_ID
    APCA_API_SECRET_KEY

What this provider can and cannot do, stated plainly because it determines how
much history the archive can ever have:

* **Forward capture is easy.** One snapshot call per underlying after the close
  returns the whole chain with NBBO quotes. That is the daily job, and it costs
  a handful of requests.

* **Backfill is not.** There is no "give me the chain as it stood on a past
  date" endpoint. Reconstructing one means enumerating the contracts that were
  listed then and pulling each one's quotes near that day's close. It works, but
  it is thousands of contracts per day and the request count grows with the
  square of your ambition. Backfill a narrow universe and a bounded strike
  range, or accept that it will take a long time.

* **Alpaca's options history begins in February 2024.** Nothing before that
  exists at this source, whatever date range you ask for.

A licensing note that matters for what gets published. Alpaca serves OPRA data
under an agreement that restricts redistribution. Derived values — implied
volatilities, greeks, SVI parameters, aggregates — are our own computations and
are ours to publish. Raw quotes are not, so backfilled chains stay local and the
public archive carries the computed surface, not the book it was fitted to.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from .occ import parse as parse_occ

DATA_BASE = "https://data.alpaca.markets"

# Two trading hosts, and a key works on exactly one of them. A paper key gets
# 401 from the live host and vice versa, which surfaces as "no contracts
# listed" rather than as an auth failure if you are not looking for it. So the
# host is resolved once, by asking, rather than assumed.
TRADING_HOSTS = (
    "https://paper-api.alpaca.markets",
    "https://api.alpaca.markets",
)
_RESOLVED_TRADING_BASE: str | None = None

# OPRA is the real feed and needs a paid options subscription. "indicative" is
# the free tier: it is derived and thinner, usable for wiring things up but not
# for a surface anyone should trust.
def _configured_feed() -> str:
    """An explicit override, if set. Otherwise empty, and we detect.

    Deliberately does NOT read ALPACA_FEED: that is the *equity* feed ("sip" or
    "iex"). The options endpoints accept only "opra" or "indicative", and
    passing an equity feed name to them returns an empty result rather than an
    error, which is a genuinely nasty way to lose an afternoon.
    """
    env = {**_dotenv(), **os.environ}
    feed = _first(env, "ALPACA_OPTIONS_FEED")
    return feed if feed in ("opra", "indicative") else ""


_RESOLVED_FEED: str | None = None


def resolve_options_feed(credentials: "Credentials") -> str:
    """Which options feed does this key actually reach? Asked once, then cached.

    Tries OPRA and falls back to the indicative feed, but never quietly: the
    fallback warns, and the chosen feed is written onto every row it produces.
    OPRA is the real consolidated quote; "indicative" is a free derived
    estimate of one. A surface fitted to an indicative quote is a surface
    fitted to an estimate, and a reader must be able to tell which they are
    looking at from the data itself, not from a note somebody remembered to
    write.
    """
    global _RESOLVED_FEED
    if _RESOLVED_FEED:
        return _RESOLVED_FEED
    override = _configured_feed()
    if override:
        _RESOLVED_FEED = override
        return override

    for feed in ("opra", "indicative"):
        try:
            r = requests.get(
                f"{DATA_BASE}/v1beta1/options/snapshots/SPY",
                headers=credentials.headers,
                params={"feed": feed, "limit": 1},
                timeout=30,
            )
        except Exception:
            continue
        if r.ok:
            if feed != "opra":
                print(
                    f"  WARNING: OPRA is not available on this key; using the "
                    f"{feed!r} feed.\n"
                    "           It is derived, not the consolidated NBBO. Every "
                    "row is tagged with it.",
                    file=sys.stderr,
                )
            _RESOLVED_FEED = feed
            return feed
    raise AlpacaNotConfigured("No options feed is readable with these credentials.")

INGEST_VERSION = "alpaca-1"

# Alpaca's documented limit is 200 requests/minute on the free tier and higher
# on paid. We pace conservatively; a backfill that trips a rate limit and gets
# throttled is slower than one that never does.
MIN_INTERVAL_SECONDS = float(os.environ.get("ALPACA_MIN_INTERVAL", "0.35"))

# Earliest date Alpaca has options data for. Asking for less is an error worth
# raising rather than silently returning an empty chain.
OPTIONS_HISTORY_START = date(2024, 2, 1)


class AlpacaNotConfigured(RuntimeError):
    """Raised when credentials are absent. Never falls back to synthetic data."""


ENV_FILE = Path(".env")


def _dotenv(path: Path = ENV_FILE) -> dict:
    """Parse a local .env. Deliberately minimal, and never logged."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return out


def _first(env: dict, *names: str) -> str:
    for n in names:
        v = (env.get(n) or "").strip()
        if v:
            return v
    return ""


@dataclass(frozen=True)
class Credentials:
    key_id: str
    secret_key: str

    @classmethod
    def from_env(cls) -> "Credentials":
        """Read credentials from the environment, or from a local .env.

        Both naming conventions are accepted. Alpaca's own SDK uses
        APCA_API_KEY_ID / APCA_API_SECRET_KEY; a great deal of code in the wild
        (including other projects on this machine) uses ALPACA_API_KEY /
        ALPACA_SECRET_KEY. Supporting both costs four lines and saves an hour of
        wondering why a key that is definitely set is definitely not found.
        """
        env = {**_dotenv(), **os.environ}    # real environment wins

        key = _first(env, "APCA_API_KEY_ID", "ALPACA_API_KEY", "ALPACA_KEY_ID")
        secret = _first(env, "APCA_API_SECRET_KEY", "ALPACA_SECRET_KEY", "ALPACA_API_SECRET")

        if not key or not secret:
            raise AlpacaNotConfigured(
                "Alpaca credentials missing.\n"
                "Set them in the environment, or write a .env beside the "
                "Makefile containing:\n"
                "    APCA_API_KEY_ID=...\n"
                "    APCA_API_SECRET_KEY=...\n"
                "ALPACA_API_KEY / ALPACA_SECRET_KEY are accepted too.\n\n"
                "There is deliberately no synthetic fallback here: a pipeline "
                "that quietly substitutes generated data for market data is the "
                "single worst failure this project could have."
            )
        return cls(key, secret)

    @property
    def headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "accept": "application/json",
        }


def resolve_trading_base(credentials: "Credentials") -> str:
    """Which trading host does this key belong to? Asked once, then cached."""
    global _RESOLVED_TRADING_BASE
    if _RESOLVED_TRADING_BASE:
        return _RESOLVED_TRADING_BASE
    for host in TRADING_HOSTS:
        try:
            r = requests.get(f"{host}/v2/account", headers=credentials.headers, timeout=30)
        except Exception:
            continue
        if r.ok:
            _RESOLVED_TRADING_BASE = host
            return host
    raise AlpacaNotConfigured(
        "These credentials were rejected by both the paper and live trading "
        "hosts. The data API may still accept them, but contract metadata "
        "(and therefore open interest) will be unavailable."
    )


class AlpacaClient:
    """Thin, paced HTTP client. One place for auth, retry and pagination."""

    def __init__(self, credentials: Credentials | None = None, feed: str | None = None):
        self.credentials = credentials or Credentials.from_env()
        self.feed = feed or resolve_options_feed(self.credentials)
        self.session = requests.Session()
        self.session.headers.update(self.credentials.headers)
        self._last_call = 0.0
        self.request_count = 0
        self._trading_base: str | None = None

    @property
    def trading_base(self) -> str:
        if self._trading_base is None:
            self._trading_base = resolve_trading_base(self.credentials)
        return self._trading_base

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < MIN_INTERVAL_SECONDS:
            time.sleep(MIN_INTERVAL_SECONDS - elapsed)
        self._last_call = time.monotonic()

    def get(self, url: str, params: dict, *, attempts: int = 4) -> dict:
        """GET with pacing and backoff on 429/5xx."""
        for attempt in range(attempts):
            self._pace()
            response = self.session.get(url, params=params, timeout=60)
            self.request_count += 1

            if response.status_code == 429 or response.status_code >= 500:
                # Respect Retry-After when the server sends one.
                wait = float(response.headers.get("Retry-After", 2 ** attempt))
                if attempt == attempts - 1:
                    response.raise_for_status()
                time.sleep(min(wait, 30))
                continue

            if response.status_code == 401:
                raise AlpacaNotConfigured(
                    "Alpaca rejected the credentials (401). Check the key pair, "
                    "and that the account has an options data subscription if "
                    f"the feed is {self.feed!r}."
                )
            response.raise_for_status()
            return response.json()
        raise RuntimeError("unreachable")

    def paginate(self, url: str, params: dict, key: str):
        """Yield each page's payload, following Alpaca's page tokens."""
        params = dict(params)
        while True:
            payload = self.get(url, params)
            yield payload
            token = payload.get("next_page_token")
            if not token:
                return
            params["page_token"] = token


def _row_from_snapshot(
    symbol: str,
    snap: dict,
    *,
    underlying: str,
    spot: float | None,
    snapshot_ts: datetime,
    meta: dict | None = None,
) -> dict | None:
    """Map one Alpaca snapshot entry onto the archive's schema (spec 4.3)."""
    try:
        occ = parse_occ(symbol)
    except ValueError:
        return None

    quote = snap.get("latestQuote") or {}
    trade = snap.get("latestTrade") or {}
    daily = snap.get("dailyBar") or {}
    meta = meta or {}

    bid = quote.get("bp")
    ask = quote.get("ap")
    if bid is None and ask is None:
        return None

    quote_ts = quote.get("t")
    if isinstance(quote_ts, str):
        try:
            quote_ts = datetime.fromisoformat(quote_ts.replace("Z", "+00:00"))
        except ValueError:
            quote_ts = None

    return {
        "snapshot_ts": snapshot_ts,
        "quote_ts": quote_ts,
        "underlying": underlying,
        "expiry": occ.expiry,
        "strike": float(occ.strike),
        "right": occ.right,
        "bid": float(bid or 0.0),
        "ask": float(ask or 0.0),
        "last": float(trade.get("p") or 0.0),
        # Volume comes from the day's bar. Open interest is not in the snapshot
        # at all — it arrives from the contracts endpoint and is merged in as
        # `meta`. Both stay None when genuinely absent rather than defaulting to
        # zero: a zero would become a real number downstream, and every
        # open-interest metric (gamma exposure above all) depends on being able
        # to tell "no contracts open" from "we do not know".
        "volume": int(daily["v"]) if daily.get("v") is not None else None,
        "open_interest": meta.get("open_interest"),
        # Open interest is previous-day settled. Its effective date travels
        # with it so nothing downstream can mistake it for today's positioning.
        "oi_effective_date": meta.get("open_interest_date"),
        "multiplier": int(meta.get("size") or 100),
        "is_adjusted": occ.is_adjusted or int(meta.get("size") or 100) != 100,
        "spot": spot,
        "source": "ALPACA",
        "feed": meta.get("_feed"),
        "ingest_version": INGEST_VERSION,
        "occ_symbol": occ.symbol,
    }


class AlpacaProvider:
    """Chain snapshots and underlying bars, in the archive's schema."""

    name = "alpaca"

    def __init__(self, client: AlpacaClient | None = None):
        self.client = client or AlpacaClient()

    # --- forward capture: the daily job -----------------------------------

    def fetch_chain(
        self, underlying: str, *, asof: date | None = None, limit: int = 1000
    ) -> list[dict]:
        """The current full chain for one underlying.

        This is what the daily job calls after the close. It is a handful of
        paginated requests, not thousands.
        """
        asof = asof or date.today()
        snapshot_ts = datetime.now(timezone.utc)
        spot = self.fetch_spot(underlying)

        metadata = self.fetch_contract_metadata(underlying)

        rows: list[dict] = []
        url = f"{DATA_BASE}/v1beta1/options/snapshots/{underlying.upper()}"
        params = {"feed": self.client.feed, "limit": limit}

        for page in self.client.paginate(url, params, "snapshots"):
            for symbol, snap in (page.get("snapshots") or {}).items():
                meta = dict(metadata.get(symbol) or {})
                meta["_feed"] = self.client.feed
                row = _row_from_snapshot(
                    symbol, snap, underlying=underlying.upper(),
                    spot=spot, snapshot_ts=snapshot_ts, meta=meta,
                )
                if row is not None:
                    rows.append(row)
        return rows

    def fetch_contract_metadata(self, underlying: str) -> dict[str, dict]:
        """Open interest, contract size and settlement style, by OCC symbol.

        The snapshot endpoint does not carry open interest, so without this the
        whole gamma-exposure section of the archive would be uncomputable. It is
        one paginated call per underlying, which is cheap next to the chain
        itself.
        """
        url = f"{self.client.trading_base}/v2/options/contracts"
        # The expiry filter is not optional. Without it this endpoint returns
        # only the nearest expiry — 852 contracts of a 12,534-contract SPY
        # chain — and it does so with a 200 and no next_page_token, so it looks
        # like the complete answer. Open interest then lands on 4% of rows
        # instead of 76%, and every gamma figure is computed from almost
        # nothing while appearing to have worked.
        params = {
            "underlying_symbols": underlying.upper(),
            "expiration_date_gte": date.today().isoformat(),
            "limit": 10000,
        }
        out: dict[str, dict] = {}
        for page in self.client.paginate(url, params, "option_contracts"):
            for contract in page.get("option_contracts") or []:
                oi = contract.get("open_interest")
                out[contract["symbol"]] = {
                    "open_interest": int(oi) if oi is not None else None,
                    "open_interest_date": contract.get("open_interest_date"),
                    "size": contract.get("size"),
                    "style": contract.get("style"),
                }
        return out

    def fetch_spot(self, underlying: str) -> float | None:
        """Latest trade price for the underlying."""
        url = f"{DATA_BASE}/v2/stocks/{underlying.upper()}/trades/latest"
        try:
            payload = self.client.get(url, {})
            return float(payload["trade"]["p"])
        except Exception:
            return None

    _equity_feed: str | None = None

    def equity_feed(self) -> str:
        """Which equity feed this key can read. SIP if entitled, else IEX.

        ALPACA_FEED is read here and only here — this is the equity feed, and
        the setting is frequently aspirational: a config saying "sip" does not
        confer a SIP subscription, and the request 403s rather than degrading.
        IEX carries the same OHLC for a liquid name; its *volume* is only the
        IEX share of consolidated, so volume-based figures from it are
        understated and should not be published as consolidated.
        """
        if self._equity_feed:
            return self._equity_feed
        env = {**_dotenv(), **os.environ}
        preferred = _first(env, "ALPACA_EQUITY_FEED", "ALPACA_FEED") or "sip"
        for feed in (preferred, "iex"):
            r = requests.get(
                f"{DATA_BASE}/v2/stocks/bars",
                headers=self.client.credentials.headers,
                # The probe window must include *today*. Free tiers commonly
                # permit historical SIP while refusing recent SIP ("subscription
                # does not permit querying recent SIP data"), so a probe over an
                # old window passes and the real request then fails. Test what
                # you are about to ask for.
                params={"symbols": "SPY", "timeframe": "1Day",
                        "start": (date.today() - timedelta(days=5)).isoformat(),
                        "end": date.today().isoformat(),
                        "feed": feed, "limit": 1},
                timeout=30,
            )
            if r.ok:
                if feed != preferred:
                    print(
                        f"  note: equity feed {preferred!r} is not entitled; "
                        f"using {feed!r}. OHLC is comparable for liquid names; "
                        "volume is IEX-only.",
                        file=sys.stderr,
                    )
                self._equity_feed = feed
                return feed
        raise AlpacaNotConfigured("No equity feed is readable with these credentials.")

    def fetch_underlying_bars(
        self, underlying: str, start: date, end: date
    ) -> list[dict]:
        """Daily OHLCV bars, for the realized-volatility estimators."""
        url = f"{DATA_BASE}/v2/stocks/bars"
        params = {
            "symbols": underlying.upper(),
            "timeframe": "1Day",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "adjustment": "split",
            "feed": self.equity_feed(),
            "limit": 10000,
        }
        out: list[dict] = []
        for page in self.client.paginate(url, params, "bars"):
            for bar in (page.get("bars") or {}).get(underlying.upper(), []):
                out.append(
                    {
                        "date": datetime.fromisoformat(
                            bar["t"].replace("Z", "+00:00")
                        ).date(),
                        "open": float(bar["o"]),
                        "high": float(bar["h"]),
                        "low": float(bar["l"]),
                        "close": float(bar["c"]),
                        "volume": int(bar["v"]),
                    }
                )
        return out

    # --- backfill: the expensive path -------------------------------------

    def list_contracts(
        self,
        underlying: str,
        *,
        expiry_after: date,
        expiry_before: date,
        strike_min: float | None = None,
        strike_max: float | None = None,
    ) -> list[str]:
        """OCC symbols listed for an underlying within an expiry window."""
        url = f"{self.client.trading_base}/v2/options/contracts"
        # Expired contracts are only returned by status="inactive". The
        # documented status="all" returns an empty list, so asking for "all"
        # silently loses every historical contract — which reads as "no data
        # for that date" and is the single easiest way to conclude a backfill
        # is impossible when it is not.
        expired = expiry_before < date.today()
        params = {
            "underlying_symbols": underlying.upper(),
            "expiration_date_gte": expiry_after.isoformat(),
            "expiration_date_lte": expiry_before.isoformat(),
            "status": "inactive" if expired else "active",
            "limit": 10000,
        }
        if strike_min is not None:
            params["strike_price_gte"] = strike_min
        if strike_max is not None:
            params["strike_price_lte"] = strike_max

        symbols: list[str] = []
        for page in self.client.paginate(url, params, "option_contracts"):
            for contract in page.get("option_contracts") or []:
                symbols.append(contract["symbol"])
        return symbols

    def fetch_closing_quotes(
        self, symbols: list[str], on: date, *, window_minutes: int = 20
    ) -> dict[str, dict]:
        """Last quote before the close on ``on``, per symbol.

        Requests a narrow window ending at the close rather than the whole
        session: the quotes endpoint is a firehose, and a full day of NBBO for a
        few thousand contracts is a great deal of data to download in order to
        keep the last row of each.
        """
        if on < OPTIONS_HISTORY_START:
            raise ValueError(
                f"Alpaca options history begins {OPTIONS_HISTORY_START}; "
                f"asked for {on}"
            )

        # 20:00 UTC is 16:00 ET during daylight saving, 21:00 UTC otherwise.
        # The calendar gate resolves the real close; this is the request window.
        close_utc = datetime(on.year, on.month, on.day, 21, 0, tzinfo=timezone.utc)
        start = close_utc - timedelta(minutes=window_minutes)

        latest: dict[str, dict] = {}
        url = f"{DATA_BASE}/v1beta1/options/quotes"

        # Alpaca caps the symbols per request; chunk conservatively.
        for i in range(0, len(symbols), 100):
            chunk = symbols[i : i + 100]
            params = {
                "symbols": ",".join(chunk),
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": close_utc.isoformat().replace("+00:00", "Z"),
                "limit": 10000,
                "feed": self.client.feed,
            }
            for page in self.client.paginate(url, params, "quotes"):
                for symbol, quotes in (page.get("quotes") or {}).items():
                    if quotes:
                        # Quotes arrive in time order; the last is the closest
                        # to the close.
                        latest[symbol] = quotes[-1]
        return latest
