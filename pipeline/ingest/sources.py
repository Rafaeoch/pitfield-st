"""Cached HTTP fetching for the free public data sources.

Every external source the study depends on is public, unauthenticated and free.
That is deliberate: the celestial half of this project needs no data licence, so
it can be reproduced by anyone who clones the repository.

Two rules this module enforces:

* **Cache on disk, keyed by URL.** Re-running the study must not re-hammer NOAA
  or the Royal Observatory of Belgium. Their generosity in serving these files
  without a key is not an invitation to poll them in a loop.
* **Record where every byte came from.** Each cached file gets a sidecar with the
  URL, the fetch timestamp and the SHA-256 of the payload, so a published result
  can be traced to the exact bytes it was computed from.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

CACHE_DIR = Path("data/raw")

# Identify ourselves. Anonymous scrapers are what get sources locked down, and
# these particular sources are a public good worth not spoiling.
USER_AGENT = (
    "PitfieldStResearch/0.1 (public research archive; "
    "contact via repository issues)"
)

DEFAULT_TIMEOUT = 60


@dataclass(frozen=True)
class Fetched:
    """A cached payload plus its provenance."""

    path: Path
    url: str
    fetched_at: str
    sha256: str
    from_cache: bool

    @property
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8", errors="replace")

    @property
    def content(self) -> bytes:
        return self.path.read_bytes()


def _slug(url: str) -> str:
    """Stable filename for a URL. The hash keeps query strings distinguishable."""
    digest = hashlib.sha256(url.encode()).hexdigest()[:12]
    tail = url.rstrip("/").split("/")[-1][:40].replace("?", "_").replace("&", "_")
    return f"{tail or 'payload'}.{digest}"


# A blip is not an outage. These are the failures worth one more attempt.
RETRY_ON = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
)
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2.0


def _get_with_retry(url: str, *, timeout: int) -> requests.Response:
    """GET with a small bounded retry on transient failures only.

    Deliberately narrow. A 404 or a 403 is the source answering, and answering
    the same way however many times you ask; retrying it wastes a source's
    goodwill and hides a real error behind a delay.
    """
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=timeout
            )
            if response.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
                last = requests.exceptions.HTTPError(
                    f"{response.status_code} from {url}", response=response
                )
            else:
                response.raise_for_status()
                return response
        except RETRY_ON as exc:
            last = exc
            if attempt == MAX_ATTEMPTS:
                raise
        # Linear, not exponential: three attempts two seconds apart is polite
        # to a free public source and still fits inside a job's timeout.
        time.sleep(BACKOFF_SECONDS * attempt)

    assert last is not None
    raise last


def fetch(
    url: str,
    *,
    cache_dir: Path = CACHE_DIR,
    refresh: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    params: dict | None = None,
) -> Fetched:
    """Fetch a URL, caching the body on disk.

    ``refresh=True`` forces a re-download; otherwise a cached copy is returned
    untouched. Nothing here retries aggressively — if a source is down, the
    right response is to fail loudly and leave the archive with a recorded gap,
    not to silently serve stale data as though it were today's.

    "Not aggressively" is not "not at all". A read timeout or a 5xx is a blip,
    not a source being down, and the two are worth telling apart: a single
    slow second from FRED once failed a deploy outright while the same
    endpoint answered in 0.19s a minute later. So transient failures get a
    small bounded retry with backoff, and everything else — 404, 403, a bad
    payload — still fails on the first try, because those are answers, not
    accidents. When the retries are exhausted the exception propagates
    unchanged and the caller still records a gap.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    if params:
        prepared = requests.Request("GET", url, params=params).prepare()
        url = prepared.url

    base = cache_dir / _slug(url)
    body_path = base.with_suffix(base.suffix + ".body")
    meta_path = base.with_suffix(base.suffix + ".meta.json")

    if body_path.exists() and meta_path.exists() and not refresh:
        meta = json.loads(meta_path.read_text())
        return Fetched(
            path=body_path,
            url=meta["url"],
            fetched_at=meta["fetched_at"],
            sha256=meta["sha256"],
            from_cache=True,
        )

    response = _get_with_retry(url, timeout=timeout)

    body_path.write_bytes(response.content)
    digest = hashlib.sha256(response.content).hexdigest()
    fetched_at = datetime.now(timezone.utc).isoformat()

    meta_path.write_text(
        json.dumps(
            {
                "url": url,
                "fetched_at": fetched_at,
                "sha256": digest,
                "bytes": len(response.content),
                "status": response.status_code,
            },
            indent=2,
        )
    )
    return Fetched(
        path=body_path,
        url=url,
        fetched_at=fetched_at,
        sha256=digest,
        from_cache=False,
    )


def provenance(cache_dir: Path = CACHE_DIR) -> list[dict]:
    """Every cached source, for publication alongside the results."""
    out = []
    for meta_path in sorted(cache_dir.glob("*.meta.json")):
        try:
            out.append(json.loads(meta_path.read_text()))
        except json.JSONDecodeError:
            continue
    return out
