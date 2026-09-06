"""A reading list, assembled from OpenAlex.

Why not Google Scholar. It has no public API, its terms forbid programmatic
access, and it blocks scrapers within a handful of requests. A page built on it
would break quietly and deserve to. OpenAlex is the right source instead: an
open catalogue of roughly 250 million works, CC0 licensed, no key required, and
explicitly built to be queried.

Two query details that decide whether this is useful or noise:

* **Relevance sort, never citation sort.** Sorting a loose full-text match by
  citation count returns the most-cited papers that happen to match anything —
  a query for "implied volatility surface" came back with plastics chemistry
  and polymer degradation, because those papers are cited tens of thousands of
  times and matched on a stray word. Relevance ordering returns Gatheral.
* **Paced requests.** OpenAlex is free and generous and answers 429 when
  hammered. One request at a time, with a courtesy delay and a mailto, is both
  polite and faster than being throttled.

Abstracts are shown as short excerpts with a link to the source, not
reproduced in full: the catalogue metadata is CC0, the abstract text belongs to
its publisher.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, asdict

import requests

OPENALEX = "https://api.openalex.org/works"
MAILTO = "research@pitfield.st"      # identifies us in the polite pool
PACE_SECONDS = 2.0
MAX_ATTEMPTS = 5
EXCERPT_CHARS = 260

# The reading list is curated by topic, not scraped by keyword soup. Each entry
# maps to something this project actually does, which is the test of whether a
# paper belongs here at all.
TOPICS = [
    ("surface", "Volatility surfaces and calibration",
     "implied volatility surface parameterisation arbitrage free",
     "The object this archive fits every day. Gatheral's SVI is the "
     "parameterisation used here."),
    ("arbitrage", "Static arbitrage conditions",
     "no arbitrage conditions implied volatility butterfly calendar spread",
     "Durrleman's condition and the calendar constraint are checked on every "
     "surface published."),
    ("vrp", "Variance risk premium",
     "variance risk premium implied realized volatility spread",
     "The replication target: implied variance sells above subsequent realized "
     "variance, persistently."),
    ("realized", "Realized volatility estimation",
     "realized volatility estimator range based high low open close",
     "Parkinson, Garman-Klass, Rogers-Satchell and Yang-Zhang are all computed "
     "here; these are their sources."),
    ("modelfree", "Model-free implied variance",
     "model free implied variance swap replication VIX methodology",
     "The VIX construction, which this archive publishes beside its parametric "
     "fit as a diagnostic."),
    ("skew", "Option-implied skewness",
     "option implied skewness risk neutral distribution crash risk",
     "What the wings are charging for tail risk, and whether it predicts "
     "anything."),
    ("gamma", "Dealer hedging and gamma exposure",
     "option dealer hedging gamma exposure market maker inventory price impact",
     "The literature behind the gamma-exposure sign convention this archive "
     "flags as an assumption rather than a fact."),
    ("momentum", "Momentum in asset returns",
     "momentum returns cross section stocks Jegadeesh Titman",
     "The 12-1 construction used in the momentum metrics, and the reversal "
     "that motivates the one-month skip."),
    ("datamining", "Data mining and multiple testing",
     "data snooping multiple hypothesis testing false discovery finance "
     "backtest overfitting",
     "The methodological spine of the celestial study, and the reason it is "
     "pre-registered."),
    ("lunar", "Lunar phase and returns",
     "lunar phase moon stock returns behavioural finance",
     "Tested directly in the celestial study. Yuan and Zheng is the paper "
     "being replicated against."),
    ("seasonal", "Seasonal affective disorder and markets",
     "seasonal affective disorder daylight length stock returns",
     "Kamstra, Kramer and Levi. Tier 1 of the study, because there is a "
     "proposed mechanism."),
    ("geomagnetic", "Geomagnetic activity and returns",
     "geomagnetic storms geomagnetic activity stock returns mood",
     "Krivelyova and Robotti. The specification the study replicates first."),
]


@dataclass(frozen=True)
class Paper:
    id: str
    topic: str
    title: str
    authors: list[str]
    year: int | None
    venue: str | None
    cited_by: int
    doi: str | None
    url: str | None
    open_access: bool
    excerpt: str | None
    # The full abstract is kept for the summariser to read and is NOT published.
    # OpenAlex distributes abstracts as an inverted index precisely so the text
    # is not redistributed verbatim; republishing a reconstructed abstract would
    # undo that. The site shows a short excerpt for identification, our own
    # generated note, and a link to the paper itself.
    abstract_full: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _reconstruct_abstract(inverted: dict | None, full: bool = False) -> str | None:
    """OpenAlex stores abstracts as an inverted index. Rebuild it.

    ``full=False`` truncates to an identifying snippet, which is what gets
    published. The full text is used only as input to the summariser and stays
    on disk.
    """
    if not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for word, spots in inverted.items():
        for spot in spots:
            positions.append((spot, word))
    if not positions:
        return None
    text = " ".join(word for _, word in sorted(positions))
    if full:
        return text
    if len(text) <= EXCERPT_CHARS:
        return text
    cut = text[:EXCERPT_CHARS].rsplit(" ", 1)[0]
    return cut + "…"


def _paper_from_work(work: dict, topic: str) -> Paper | None:
    title = (work.get("title") or "").strip()
    if not title:
        return None
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    return Paper(
        id=(work.get("id") or "").rsplit("/", 1)[-1],
        topic=topic,
        title=title,
        authors=[
            a["author"]["display_name"]
            for a in (work.get("authorships") or [])[:4]
            if a.get("author", {}).get("display_name")
        ],
        year=work.get("publication_year"),
        venue=source.get("display_name"),
        cited_by=int(work.get("cited_by_count") or 0),
        doi=work.get("doi"),
        url=work.get("doi") or location.get("landing_page_url"),
        open_access=bool((work.get("open_access") or {}).get("is_oa")),
        excerpt=_reconstruct_abstract(work.get("abstract_inverted_index")),
        abstract_full=_reconstruct_abstract(work.get("abstract_inverted_index"), full=True),
    )


# OpenAlex asks to be identified in the User-Agent, and puts requests that do
# so into a faster pool. A mailto query parameter alone is not enough to avoid
# throttling in practice.
HEADERS = {"User-Agent": f"PitfieldSt/0.1 (mailto:{MAILTO})"}


def fetch_topic(query: str, topic: str, limit: int = 8) -> list[Paper]:
    """One topic's papers, ordered by OpenAlex relevance.

    No citation sort. See the module docstring: sorting a loose match by
    citations returns whatever is most cited, not what is most relevant.

    Retries on 429 with exponential backoff. A free service answering "slow
    down" is not an error to propagate — it is an instruction to follow, and
    the difference between honouring it and not is eight of twelve topics
    silently missing from the page.
    """
    delay = PACE_SECONDS
    for attempt in range(MAX_ATTEMPTS):
        response = requests.get(
            OPENALEX,
            params={"search": query, "filter": "type:article",
                    "per-page": limit, "mailto": MAILTO},
            headers=HEADERS,
            timeout=60,
        )
        if response.status_code == 429:
            wait = float(response.headers.get("Retry-After", delay))
            time.sleep(min(wait, 30.0))
            delay *= 2
            continue
        break
    response.raise_for_status()
    papers = []
    for work in response.json().get("results", []):
        paper = _paper_from_work(work, topic)
        if paper is not None:
            papers.append(paper)
    return papers


def build(limit_per_topic: int = 8) -> dict:
    """The whole reading list, one topic at a time, paced."""
    sections, seen = [], set()
    for key, name, query, why in TOPICS:
        try:
            papers = fetch_topic(query, key, limit_per_topic)
        except Exception as exc:
            print(f"  {key:<12} FAILED {type(exc).__name__}: {exc}")
            sections.append({"key": key, "name": name, "why": why,
                             "query": query, "papers": [], "error": str(exc)})
            continue

        # A paper that already appeared under an earlier topic stays there. The
        # topics are ordered by how central they are to this project, so the
        # first placement is the most apt one.
        unique = []
        for paper in papers:
            marker = (paper.title.lower(), paper.year)
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(paper.as_dict())

        sections.append({"key": key, "name": name, "why": why,
                         "query": query, "papers": unique})
        print(f"  {key:<12} {len(unique):>2} papers  ({name})")
        time.sleep(PACE_SECONDS)

    return {
        "source": "OpenAlex",
        "source_url": "https://openalex.org",
        "licence": "Catalogue metadata CC0. Abstract excerpts belong to their publishers.",
        "n_papers": sum(len(s["papers"]) for s in sections),
        "sections": sections,
    }
