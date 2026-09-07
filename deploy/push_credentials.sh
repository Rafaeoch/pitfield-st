#!/usr/bin/env bash
# Copy the Alpaca credentials from the local .env into /etc/pitfield/env.
#
#   ./deploy/push_credentials.sh root@89.167.26.72
#
# Run by a person, not by tooling. The values go straight from your .env to the
# server over SSH: they are never printed, never passed as a command argument
# (which would expose them in the server's process list), and never written to
# a temporary file on either machine. Only the character counts are echoed, so
# you can confirm it worked without putting a secret on your screen.
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET="${1:-}"
[ -z "$TARGET" ] && { echo "usage: deploy/push_credentials.sh <user@host>" >&2; exit 64; }
[ -f .env ] || { echo "no .env beside the Makefile" >&2; exit 1; }

value_of() {
  for name in "$@"; do
    local v
    v=$(grep -E "^[[:space:]]*${name}[[:space:]]*=" .env | head -1 | cut -d= -f2- \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' \
              -e "s/^'//" -e "s/'\$//" | tr -d '\r')
    [ -n "$v" ] && { printf '%s' "$v"; return 0; }
  done
  return 1
}

# The provider accepts either convention; so do we.
KEY=$(value_of APCA_API_KEY_ID ALPACA_API_KEY ALPACA_KEY_ID) || {
  echo "no Alpaca key id found in .env" >&2; exit 1; }
SECRET=$(value_of APCA_API_SECRET_KEY ALPACA_SECRET_KEY ALPACA_API_SECRET) || {
  echo "no Alpaca secret found in .env" >&2; exit 1; }

echo "found key id (${#KEY} chars) and secret (${#SECRET} chars); sending to $TARGET"

# Piped over stdin and merged server-side. Other keys in the file -- the
# optional Anthropic key, the alert webhook, UNDERLYINGS -- are preserved.
printf 'APCA_API_KEY_ID=%s\nAPCA_API_SECRET_KEY=%s\n' "$KEY" "$SECRET" \
  | ssh "$TARGET" 'umask 077; python3 - <<'"'"'PY'"'"'
import sys, pathlib
path = pathlib.Path("/etc/pitfield/env")
incoming = {}
for line in sys.stdin:
    if "=" in line:
        k, _, v = line.partition("=")
        incoming[k.strip()] = v.strip()

out, seen = [], set()
for line in path.read_text().splitlines():
    stripped = line.strip()
    if "=" in stripped and not stripped.startswith("#"):
        k = stripped.split("=", 1)[0].strip()
        if k in incoming:
            out.append(f"{k}={incoming[k]}")
            seen.add(k)
            continue
    out.append(line)
for k, v in incoming.items():
    if k not in seen:
        out.append(f"{k}={v}")

path.write_text("\n".join(out) + "\n")
print("  wrote /etc/pitfield/env")
for k in sorted(incoming):
    print(f"  {k} = SET ({len(incoming[k])} chars)")
PY
chown root:pitfield /etc/pitfield/env && chmod 0640 /etc/pitfield/env
ls -l /etc/pitfield/env'

echo
echo "Done. Verify the pipeline can see them:"
echo "  ssh $TARGET 'sudo -u pitfield bash -c \"set -a; . /etc/pitfield/env; set +a; cd /opt/pitfield && ./.venv/bin/python -m pipeline.ingest.probe\"'"
