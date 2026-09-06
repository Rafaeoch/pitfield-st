#!/usr/bin/env bash
# Push this working tree to a provisioned box and rebuild. Run from the laptop:
#   ./deploy/deploy.sh root@archive.pitfield.st
#
# Idempotent, and safe to run as often as you like.
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET="${1:-}"
[ -z "$TARGET" ] && { echo "usage: deploy/deploy.sh <user@host>" >&2; exit 64; }

echo "== Tests before anything leaves the laptop =="
./.venv/bin/python -m pytest tests/ -q

echo
echo "== Sync code to $TARGET:/opt/pitfield =="
# What is NOT sent matters more than what is:
#   .env      credentials belong in /etc/pitfield/env on the box, typed by hand
#   .venv     built for macOS; a Linux box must build its own
#   data      the server's captures are the real ones, never overwrite them
#   node_mod. reinstalled from the lockfile
#   dist      rebuilt from the server's own data
# rsync protects excluded paths from --delete, so the server's data/ and .venv/
# survive every deploy. That is load-bearing: --delete-excluded would erase the
# archive.
rsync -az --delete \
  --exclude '.env' \
  --exclude '.venv/' \
  --exclude 'data/' \
  --exclude 'logs/' \
  --exclude 'node_modules/' \
  --exclude 'site/dist/' \
  --exclude 'site/.astro/' \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '.DS_Store' \
  ./ "$TARGET:/opt/pitfield/"

echo
echo "== Build and publish on the server =="
ssh "$TARGET" "chown -R pitfield:pitfield /opt/pitfield && sudo -u pitfield -H /opt/pitfield/deploy/remote_build.sh"

echo
echo "== Health check =="
HOST="${TARGET#*@}"
ssh "$TARGET" 'systemctl list-timers "pitfield-*" --no-pager | head -4; \
  echo; ls -l /var/www/pitfield/current; \
  echo; curl -fsS -o /dev/null -w "local docroot: HTTP %{http_code}\n" http://127.0.0.1/ || true'
echo
echo "Deployed. Public check:  curl -sSI https://$HOST | head -1"
