# dreaming-osc-relay

Standalone OSC relay for the Dreaming installation. It observes a dream
server's WebSocket feed and re-emits it as OSC/UDP to local gear
(SuperCollider, Pure Data, TouchDesigner, lighting), speaking the same
`/dreaming/*` schema as the backend's built-in `--osc` mode.

One component, both worlds: point it at the cloud backend for build and
rehearsal, at `http://127.0.0.1:8000` at the venue. The gear hears
byte-identical messages either way.

## Run

Needs [uv](https://docs.astral.sh/uv/) (or any Python 3.10+ with
`pip install -e .`):

    uv run dreaming-osc-relay \
      --server https://<backend-host> --token <DREAM_TOKEN> \
      --osc 127.0.0.1:57120

- `--server` / `--token` also read from `DREAM_SERVER` / `DREAM_TOKEN` env.
- `--osc HOST:PORT` is repeatable for several destinations
  (default `127.0.0.1:57120`, the SuperCollider port).
- Auto-reconnects every 5s if the feed drops. Shows up as `osc-relay` in
  the backend admin's client list.

## What it emits

Per focus change: `/dreaming/work`, `/work/*`, `/composition/*`,
`/colour/*`, `/tonal/*`, `/objects/*`, `/faces/*`, `/figures/*`, `/tags`,
`/history/*` - plus the dream extras `/dreaming/voice`, `/pool`,
`/visitor`, `/absorbed`, `/done`. Recorded-dream replays fire exactly like
live dreams; reconnect catch-up history never re-fires.

The full per-work metadata comes from the bundled `records/` directory.
Without it the relay still runs with minimal work info.

## Updating from the backend

`schema.py` and `records/` are mirrors of the backend repo. After backend
schema changes:

    ./sync_from_backend.sh [path-to-dreaming-v3-backend]
