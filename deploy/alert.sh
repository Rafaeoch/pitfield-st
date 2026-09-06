#!/usr/bin/env bash
# Record a failed job somewhere durable. Called by pitfield-alert@.service.
#
# Deliberately not email: a box with no configured MTA silently drops mail, and
# an alert you believe is working but is not is worse than no alert at all.
# A file always works. The webhook is optional and off unless a URL is set.
set -uo pipefail
UNIT="${1:-unknown}"
LOG=/var/log/pitfield/failures.log
mkdir -p "$(dirname "$LOG")"

{
  echo "=== $(date -Is) — $UNIT FAILED ==="
  systemctl status "$UNIT" --no-pager --lines=0 2>&1 | head -6
  echo "--- last 25 journal lines ---"
  journalctl -u "$UNIT" --no-pager --lines=25 2>&1
  echo
} >> "$LOG"

if [ -n "${PITFIELD_ALERT_WEBHOOK:-}" ]; then
  curl -fsS -m 10 -X POST -H 'Content-Type: application/json' \
    -d "{\"text\":\"Pitfield St: $UNIT failed at $(date -Is). The archive has a gap until this is resolved.\"}" \
    "$PITFIELD_ALERT_WEBHOOK" >/dev/null 2>&1 || true
fi
