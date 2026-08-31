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

## Troubleshooting

Run `./check-network.sh` first if the relay starts but cannot reach the
server. It names the cause rather than guessing at it.

**`error: Failed to spawn: dreaming-osc-relay`** — the relay was launched
from somewhere other than this folder, so `uv` found no project. Use
`start-relay-runpod.sh`, which changes directory first.

**`ImportError: cannot import name 'OscTranslator'`** — `schema.py` has been
overwritten by `sync_from_backend.sh` running without a backend checkout
beside it. That script is a maintenance tool for after a backend schema
change; it is not a setup step and running the relay needs nothing from it.
Replace this folder with a fresh copy.

**`certificate verify failed: unable to get local issuer certificate`**, or
TLS handshakes timing out — something between the machine and the server is
inspecting or blocking TLS. `./check-network.sh` distinguishes the three
cases:

- *Not Runpod's certificate.* The network is re-signing TLS with its own
  root (common on university and corporate Macs). The relay verifies
  against the OS trust store by default, so the root the machine already
  trusts is honoured. If it still fails, that root is not in the Keychain
  either and only IT can add it; a phone hotspot is the quick way round.
- *Runpod's certificate, but unverifiable.* Same fix, same default. Use
  `--no-system-trust` to confirm the difference: with it the failure
  returns, without it the relay connects.
- *TLS fine, relay still drops.* A proxy allowing ordinary HTTPS but
  breaking the WebSocket upgrade. Nothing on this side helps; use another
  network.

None of this touches the venue, where the relay points at
`http://127.0.0.1:8000` on the same machine and no TLS is involved.

**First run needs the internet.** `uv` builds `.venv` and fetches three
packages the first time. Run it once somewhere with a connection; after
that the folder is self-contained.
