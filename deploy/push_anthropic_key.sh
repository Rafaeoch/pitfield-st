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

# Hidden input shows no feedback, so a paste that appears to do nothing invites
# pasting again. That is what happened the first time this script ran: the key
# went in three times, 324 characters, and the API returned 401. Repair the
# obvious case rather than lecturing about it.
KEY=$(printf '%s' "$KEY" | tr -d '[:space:]')
OCCURRENCES=$(printf '%s' "$KEY" | grep -o 'sk-ant-' | wc -l | tr -d ' ')
if [ "$OCCURRENCES" -gt 1 ]; then
  FIRST=${KEY#sk-ant-}
  FIRST="sk-ant-${FIRST%%sk-ant-*}"
  # Only trim when the whole thing is that one key repeated; anything else is
  # a genuinely malformed paste and should be retyped, not guessed at.
  REPEATED=""
  i=0
  while [ "$i" -lt "$OCCURRENCES" ]; do REPEATED="$REPEATED$FIRST"; i=$((i + 1)); done
  if [ "$REPEATED" = "$KEY" ]; then
    echo "  note: the key was pasted $OCCURRENCES times; using one copy"
    KEY="$FIRST"
  else
    echo "found 'sk-ant-' $OCCURRENCES times but the value is not one key repeated." >&2
    echo "Clear the paste and try again." >&2
    exit 1
  fi
fi

case "$KEY" in
  sk-ant-*) ;;
  *) echo "that does not look like an Anthropic key (expected it to start sk-ant-)" >&2; exit 1;;
esac
if [ "${#KEY}" -lt 80 ] || [ "${#KEY}" -gt 200 ]; then
  echo "key is ${#KEY} characters; an Anthropic key is around 100-110." >&2
  echo "That usually means the paste picked up extra text. Try again." >&2
  exit 1
fi

echo "sending a ${#KEY}-character key to $TARGET"
printf 'ANTHROPIC_API_KEY=%s\n' "$KEY" \
  | ssh "$TARGET" 'umask 077; python3 /opt/pitfield/deploy/merge_env.py \
      && chown root:pitfield /etc/pitfield/env \
      && chmod 0640 /etc/pitfield/env'
unset KEY

echo
echo "Verifying the key actually authenticates (no value shown):"
ssh "$TARGET" 'sudo -u pitfield bash -c "
  set -a; . /etc/pitfield/env; set +a
  cd /opt/pitfield
  ./.venv/bin/python -c \"
import os
k = os.environ.get(\\\"ANTHROPIC_API_KEY\\\", \\\"\\\")
print(f\\\"  visible to the job: {len(k)} chars\\\" if k else \\\"  NOT visible\\\")
try:
    import anthropic
    c = anthropic.Anthropic()
    c.models.list(limit=1)
    print(\"  AUTH OK - the API accepted this key\")
except ImportError:
    print(\"  anthropic package MISSING on the server\")
except Exception as e:
    print(f\"  AUTH FAILED: {type(e).__name__}: {str(e)[:120]}\")
\""'
