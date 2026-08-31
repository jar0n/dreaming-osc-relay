#!/usr/bin/env bash
# Sync the pieces this repo mirrors from the backend (the single source of
# truth): the OSC schema module, and the artwork records that enrich
# per-work messages. Run after any backend change to the /dreaming/* schema.
#
# Only useful with a backend checkout to hand. Without one it refuses and
# changes nothing: an earlier version truncated schema.py before it knew
# whether the source existed, leaving a three-comment-line module that
# imported fine and had no OscTranslator in it.
set -euo pipefail
cd "$(dirname "$0")"

BACKEND="${1:-../../dreaming-v3-backend}"
SOURCE="$BACKEND/src/dreaming/server/osc.py"

if [ ! -f "$SOURCE" ]; then
  echo "no backend at $BACKEND (looked for $SOURCE)" >&2
  echo "This script is only for updating the relay from a backend checkout." >&2
  echo "To just run the relay you need nothing from here." >&2
  exit 1
fi

# build beside the target, move into place only on success, so a failure
# part-way through cannot leave a half-written schema
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
{
  echo "# GENERATED - do not edit here."
  echo "# Copied from dreaming-v3-backend/src/dreaming/server/osc.py by"
  echo "# sync_from_backend.sh so relay and backend emit identical OSC."
  # strip the original module docstring (future-import ordering), and the
  # hub-facing forwarder (which needs the backend to import)
  sed '1,/^"""$/d' "$SOURCE" | sed '/^class DreamOscForwarder/,$d'
} > "$TMP"

if ! grep -q '^class OscTranslator' "$TMP"; then
  echo "synced schema has no OscTranslator - backend osc.py has moved on;" >&2
  echo "fix this script rather than shipping a schema the relay cannot use." >&2
  exit 1
fi
mv "$TMP" src/dreaming_osc_relay/schema.py
trap - EXIT

mkdir -p records
cp "$BACKEND"/data/artwork-data/*.json records/ 2>/dev/null || true
find records -name '._*' -delete
echo "schema synced; $(ls records | wc -l | tr -d ' ') records"
