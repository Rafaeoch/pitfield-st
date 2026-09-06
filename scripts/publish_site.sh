#!/usr/bin/env bash
# Rebuild the static site and, on a server, swap it into place atomically.
#
# This step is not optional, and the reason is easy to miss. Pages read the
# archive at BUILD time -- site/src/lib/archive.ts does fs.readFileSync, and so
# do the reading and study pages -- while the three.js components fetch their
# JSON at RUN time. On a laptop `npm run dev` re-reads on every request and the
# distinction never surfaces. Serving a prebuilt dist/ without rebuilding gives
# you the worst possible failure: the moon globe and the surface keep updating
# from fetched JSON while the homepage counters, the quality tables and the
# study results silently freeze at whatever they were on build day. A site that
# is confidently wrong in half its numbers is worse than one that is down.
#
# PITFIELD_WWW unset (a laptop): build only.
# PITFIELD_WWW set (the server): build, publish as a new release, swap symlink.
set -euo pipefail
cd "$(dirname "$0")/.."

cd site && npm run build --silent && cd ..

[ -z "${PITFIELD_WWW:-}" ] && { echo "built site/dist (no PITFIELD_WWW, not publishing)"; exit 0; }

# Publish under a timestamp and move a symlink, rather than writing into the
# directory Caddy is reading from. rsync --delete into a live docroot serves
# half-written pages for as long as the copy takes.
STAMP=$(date +%Y%m%dT%H%M%S)
REL="$PITFIELD_WWW/releases/$STAMP"
mkdir -p "$REL"
cp -a site/dist/. "$REL/"

ln -sfn "$REL" "$PITFIELD_WWW/.current.tmp"
# rename(2) over the old symlink: a reader sees either the previous release or
# this one, never neither. `mv -T` is GNU; the fallback is for running this on
# a Mac, where the brief gap does not matter because nothing is serving.
if ! mv -Tf "$PITFIELD_WWW/.current.tmp" "$PITFIELD_WWW/current" 2>/dev/null; then
  rm -f "$PITFIELD_WWW/current"
  mv "$PITFIELD_WWW/.current.tmp" "$PITFIELD_WWW/current"
fi
echo "published $STAMP"

# Keep five. Enough to roll back by hand, not enough to fill a 40GB disk.
#
# Sorted by NAME, not by mtime. Release directories are named with a sortable
# timestamp, so name order is release order and cannot be perturbed. `ls -t`
# looked equivalent and is not: `cp -a` above preserves source timestamps, and
# anything that touches an old release -- a backup, a manual look, a restore --
# reorders it. Sorting by mtime deleted the live release in testing and left
# `current` dangling, which on the server is the site going down.
#
# The explicit skip is belt and braces: whatever the sort says, never remove
# the directory `current` resolves to.
CURRENT=$(readlink "$PITFIELD_WWW/current" 2>/dev/null || true)
cd "$PITFIELD_WWW/releases"
ls -1d ./*/ 2>/dev/null | sort -r | tail -n +6 | while read -r old; do
  old=${old%/}
  [ "$(cd "$old" && pwd)" = "${CURRENT%/}" ] && continue
  rm -rf "$old"
done
