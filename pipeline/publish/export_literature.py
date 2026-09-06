"""Fetch the reading list and publish it.

Two files, deliberately:

* ``data/raw/literature_raw.json`` keeps the full abstracts. It is the input
  the summariser reads and it never ships.
* ``site/public/data/literature.json`` is what the site serves: abstracts
  reduced to identifying excerpts, plus whatever notes are already cached.

Each step has to be safe to run alone. An earlier version left the stripping and
the note-merging inside the summariser, which meant running this module by
itself silently wiped every note and published the full abstracts — the one
thing the sourcing section of the page promises it does not do.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from ..ingest.literature import build

RAW_PATH = Path("data/raw/literature_raw.json")
NOTES_PATH = Path("data/raw/paper_notes.json")


def publish(payload: dict, out_dir: Path) -> Path:
    """Strip abstracts, merge cached notes, write the site file."""
    notes = {}
    if NOTES_PATH.exists():
        try:
            notes = json.loads(NOTES_PATH.read_text())
        except json.JSONDecodeError:
            notes = {}

    attached = 0
    for section in payload["sections"]:
        for paper in section["papers"]:
            note = notes.get(paper.get("id", ""))
            if note:
                paper["note"] = note
                attached += 1
            # Never published. See the module docstring.
            paper.pop("abstract_full", None)

    payload["n_notes"] = attached
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "literature.json"
    path.write_text(json.dumps(payload, separators=(",", ":")))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("site/public/data"))
    parser.add_argument("--per-topic", type=int, default=8)
    args = parser.parse_args()

    print("Building the reading list from OpenAlex\n")
    payload = build(args.per_topic)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()

    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps(payload, separators=(",", ":")))

    path = publish(payload, args.out)
    print(f"\n{payload['n_papers']} papers across {len(payload['sections'])} topics")
    print(f"  {payload['n_notes']} already have a cached note")
    print(f"wrote {RAW_PATH} (full abstracts, local only)")
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB, published)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
