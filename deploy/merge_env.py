"""Merge KEY=VALUE lines from stdin into /etc/pitfield/env.

A separate file, and not a heredoc, for a specific reason. Written as
`python3 - <<'PY' ... PY` the heredoc *becomes* Python's stdin -- it is the
program text -- so a value piped in from the other side of the SSH connection
is read by nobody. The first version of this did exactly that, found no keys,
rewrote the file byte-identically, and printed "wrote /etc/pitfield/env".
Reporting success for a no-op is the worst outcome available.

Values are never echoed; only their lengths, so the caller can confirm the
write without putting a secret on screen.
"""

from __future__ import annotations

import pathlib
import sys

TARGET = pathlib.Path("/etc/pitfield/env")


def main() -> int:
    incoming: dict[str, str] = {}
    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if value:
            incoming[key.strip()] = value

    if not incoming:
        print("  no key=value pairs arrived on stdin; refusing to rewrite", file=sys.stderr)
        return 1

    out: list[str] = []
    seen: set[str] = set()
    for line in TARGET.read_text().splitlines():
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in incoming:
                out.append(f"{key}={incoming[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in incoming.items():
        if key not in seen:
            out.append(f"{key}={value}")

    TARGET.write_text("\n".join(out) + "\n")
    print(f"  wrote {TARGET}")
    for key in sorted(incoming):
        print(f"  {key} = SET ({len(incoming[key])} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
