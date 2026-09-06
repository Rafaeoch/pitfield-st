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

    response = requests.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=timeout
    )
    response.raise_for_status()

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
