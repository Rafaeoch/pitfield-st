#!/usr/bin/env bash
# Put an Anthropic API key into /etc/pitfield/env on the server.
#
#   ./deploy/push_anthropic_key.sh root@89.167.26.72
#
# The key is read with the terminal echo off, so it does not appear on screen,
# does not enter shell history, and is never passed as a command argument where
# the server's process list would show it. It goes over stdin through the SSH
# connection straight into the file. Only its length is reported back.
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET="${1:-}"
[ -z "$TARGET" ] && { echo "usage: deploy/push_anthropic_key.sh <user@host>" >&2; exit 64; }

printf 'Paste the Anthropic API key (input hidden), then press Enter:\n> '
IFS= read -rs KEY
printf '\n'

[ -z "$KEY" ] && { echo "nothing entered; aborting" >&2; exit 1; }
case "$KEY" in
  sk-ant-*) ;;
  *) echo "that does not look like an Anthropic key (expected it to start sk-ant-)" >&2; exit 1;;
esac
[ "${#KEY}" -lt 40 ] && { echo "key looks too short (${#KEY} chars); aborting" >&2; exit 1; }

echo "sending a ${#KEY}-character key to $TARGET"
printf 'ANTHROPIC_API_KEY=%s\n' "$KEY" \
  | ssh "$TARGET" 'umask 077; python3 /opt/pitfield/deploy/merge_env.py \
      && chown root:pitfield /etc/pitfield/env \
      && chmod 0640 /etc/pitfield/env'
unset KEY

echo
echo "Verifying the pipeline can read it (no value shown):"
ssh "$TARGET" 'sudo -u pitfield bash -c "
  set -a; . /etc/pitfield/env; set +a
  cd /opt/pitfield
  ./.venv/bin/python -c \"
import os
k = os.environ.get(\\\"ANTHROPIC_API_KEY\\\", \\\"\\\")
print(f\\\"  visible to the job: {len(k)} chars\\\" if k else \\\"  NOT visible\\\")
try:
    import anthropic; print(\\\"  anthropic package: installed\\\")
except ImportError:
    print(\\\"  anthropic package: MISSING\\\")
\""'
