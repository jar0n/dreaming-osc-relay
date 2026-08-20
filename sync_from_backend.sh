#!/usr/bin/env bash
# Sync the pieces this repo mirrors from the backend (the single source of
# truth): the OSC schema module, and the artwork records that enrich
# per-work messages. Run after any backend change to the /dreaming/* schema.
set -euo pipefail
cd "$(dirname "$0")"

BACKEND="${1:-../../dreaming-v3-backend}"

{
  echo "# GENERATED - do not edit here."
  echo "# Copied from dreaming-v3-backend/src/dreaming/server/osc.py by"
  echo "# sync_from_backend.sh so relay and backend emit identical OSC."
  # strip the original module docstring (future-import ordering), and the
  # hub-facing forwarder (which needs the backend to import)
  sed '1,/^"""$/d' "$BACKEND/src/dreaming/server/osc.py" \
    | sed '/^class DreamOscForwarder/,$d'
} > src/dreaming_osc_relay/schema.py

mkdir -p records
cp "$BACKEND"/data/artwork-data/*.json records/ 2>/dev/null || true
find records -name '._*' -delete
echo "schema synced; $(ls records | wc -l | tr -d ' ') records"
