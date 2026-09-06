"""Generate a short note on each paper's relevance to options markets.

What these notes are, stated plainly because the distinction matters:

They are written by Claude **from the paper's abstract alone**, not from the
paper. An abstract is a few hundred words the authors wrote to advertise the
work; a note derived from it can say what the paper appears to be about and how
that might bear on option pricing, and it cannot say whether the paper's method
holds up, what its data actually showed, or whether its conclusion survived
peer response. The site labels them accordingly, and every entry links to the
paper so a reader can go and check.

That labelling is not a disclaimer bolted on. A research archive whose whole
argument is that computed numbers should be traceable cannot then publish
machine-written scholarship as though someone had read the papers.

Batched, because build-time bulk work is exactly what the Batches API is for:
half price, and one poll instead of ninety round trips. Cached by paper id, so
a rebuild only pays for papers it has not seen.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

CACHE_PATH = Path("data/raw/paper_notes.json")
MODEL = "claude-opus-5"
MAX_TOKENS = 400

SYSTEM = """You write one-paragraph relevance notes for a public archive of \
computed options-market data.

You are given a paper's title and abstract. Write 2-3 sentences on how the work \
could bear on options markets specifically: pricing, implied volatility, the \
volatility surface, hedging, or the interpretation of option-implied measures.

Rules:
- Base every claim on the abstract you are given. You have not read the paper.
- If the abstract does not support a claim about options, say what the work is \
about and that its bearing on options markets is indirect. Do not invent a link.
- No hedging boilerplate, no "this paper could potentially perhaps". Write \
plainly.
- Never recommend a trade, a strategy, or a position.
- No preamble. Start with the substance."""


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=1))


def generate(papers: list[dict], cache: dict) -> dict:
    """Batch-generate notes for papers not already cached."""
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    todo = [
        p for p in papers
        if p.get("id") and p.get("abstract_full") and p["id"] not in cache
    ]
    if not todo:
        print("  every paper already has a note; nothing to generate")
        return cache

    client = anthropic.Anthropic()
    print(f"  submitting {len(todo)} papers as one batch")

    batch = client.messages.batches.create(
        requests=[
            Request(
                custom_id=p["id"],
                params=MessageCreateParamsNonStreaming(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM,
                    messages=[{
                        "role": "user",
                        "content": (
                            f"Title: {p['title']}\n\n"
                            f"Abstract: {p['abstract_full'][:4000]}"
                        ),
                    }],
                ),
            )
            for p in todo
        ]
    )
    print(f"  batch {batch.id} submitted; polling")

    while True:
        state = client.messages.batches.retrieve(batch.id)
        if state.processing_status == "ended":
            break
        counts = state.request_counts
        print(f"    {counts.processing} processing, {counts.succeeded} done")
        time.sleep(30)

    ok = failed = 0
    # Results arrive in any order — key by custom_id, never by position.
    for result in client.messages.batches.results(batch.id):
        if result.result.type != "succeeded":
            failed += 1
            continue
        text = "".join(
            b.text for b in result.result.message.content if b.type == "text"
        ).strip()
        if text:
            cache[result.custom_id] = text
            ok += 1

    print(f"  {ok} notes written, {failed} failed")
    return cache


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("site/public/data"))
    args = parser.parse_args()

    raw_path = Path("data/raw/literature_raw.json")
    if not raw_path.exists():
        print("no data/raw/literature_raw.json; run `make reading` first", file=sys.stderr)
        return 1

    # Read the raw file: the published one has had its abstracts stripped, and
    # the summariser needs them.
    payload = json.loads(raw_path.read_text())
    papers = [p for s in payload["sections"] for p in s["papers"]]
    cache = _load_cache()

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY")) or bool(
        os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )
    if has_key:
        try:
            cache = generate(papers, cache)
            _save_cache(cache)
        except Exception as exc:
            print(f"  generation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    else:
        print("  no ANTHROPIC_API_KEY set — using whatever notes are cached")

    payload["note_model"] = MODEL
    payload["note_basis"] = (
        "Written by Claude from each paper's abstract alone, not from the paper."
    )
    from .export_literature import publish

    path = publish(payload, args.data)
    print(f"\n{payload['n_notes']} of {len(papers)} papers carry a note")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
