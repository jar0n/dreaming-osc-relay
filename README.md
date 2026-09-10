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

## Finding the server: `--discover`

At the venue there is no address to write down. The two machines are
joined by a direct cable with no DHCP, so each end self-assigns a
`169.254.x.y` picked afresh every time it boots or the cable is
replugged. Rather than editing a launcher script at the venue with the
lid open, let the relay go and find it:

    uv run dreaming-osc-relay --discover --token <DREAM_TOKEN>

Bare `--discover` sweeps the networks this machine is actually on - the
cable's `169.254.0.0/16` at the venue, the office LAN in rehearsal, and
it needs telling neither apart. Name a range instead if you want one:

    --discover '169.254.*.*'        # quote it, or the shell eats the *
    --discover 192.168.1.0/24

It sweeps for anything accepting connections on port 8000, then opens a
real WebSocket to each candidate before settling on it: port 8000 is a
popular port, and "something answered" is not the same as "the dream
server". A whole `169.254.0.0/16` takes about 30 seconds to rule out and
usually far less to find something; a `/24` takes about a second.

Two things follow from doing it this way, both wanted:

- The machines can be started in either order. With nothing to talk to
  the relay just sweeps again every 5s until the server appears.
- The address is never trusted for good. If the feed drops three times
  running, the relay takes it that the server has moved (rebooted, or the
  cable replugged) and sweeps for it again, rather than reconnecting
  forever to an address nobody is answering on.

`--discover` overrides `--server`, so `start-relay-runpod.sh` and the
cloud are unaffected.

## What it emits

As a dream begins, `/dreaming/dreammode 1` (and `0` as it ends).
Per focus change: `/dreaming/work`, `/work/*`, `/composition/*`,
`/colour/*`, `/tonal/*`, `/objects/*`, `/faces/*`, `/figures/*`, `/tags`,
`/pct/*`, `/mood/*`, `/history/*`, then `/hop/*` and `/link` for the hop
itself, the four affect params as last felt, and `/dreaming/stab focus <pid>`.
Between works: `/dreaming/voice`, `/pool`, `/visitor`, `/absorbed`,
`/affect` (bundled) with `/affect/valence|arousal|dominance|approach` (one
float each, 0..1 with rest at 0.5), `/dreaming/stab pair <pid>` as the crossing is said and the next
work hangs beside it, `/dreaming/stab rest` and `/done` as the dream ends,
then the affect params once more at the atlas pose as the wall resets to
the atlas: `/affect 0 0 0 1` and the four single addresses, 0 on valence,
arousal and dominance, 1 on approach (a set time after the end, 0 by
default, dropped if the next dream begins first; this replaced
`/dreaming/sssh atlas` on 10 Sep 2026). Recorded-dream
replays fire exactly like live dreams; reconnect catch-up history never
re-fires.

Two profiles. `web` is the faithful OSCManager.js schema with raw values;
`supercollider` rewrites the same addresses into the ranges the venue patch
reads (percentile ranks, key without suffix, tags in its vocabulary), each
rewrite switchable on its own. The relay follows the server's admin
settings for the profile, the corrections and the `sssh` delay: read once
on connect from `/review/api/dream/status` and again on every live `status`
event, applied from the next work. `--profile web|supercollider` pins the
profile instead. The backend's dream browser (`/review/dreams`) shows what
any recorded dream sends under either profile, message for message.

The full per-work metadata comes from the bundled `records/` directory.
Without it the relay still runs with minimal work info.

## Updating from the backend

`schema.py` and `records/` are mirrors of the backend repo (the schema is
`src/dreaming/server/osc.py` there, header comment included). After backend
schema changes:

    ./sync_from_backend.sh [path-to-dreaming-v3-backend]

## Troubleshooting

Run `./check-network.sh` first if the relay starts but cannot reach the
server. It names the cause rather than guessing at it.

**`--discover` finds nothing at the venue.** The sweep prints what it
believes about the network before it starts. `no route to that network
from here` or `on no network worth sweeping` means macOS has not
self-assigned an address on the cable: check it is seated at both ends,
and give it fifteen seconds - self-assignment is not instant. If instead
it says `this machine is 169.254.x.y` and still finds nothing, the cable
is up and the far end is not: the server is not running, or not yet
listening on port 8000.

**`is a dream server but rejected the token`.** Discovery worked and the
address is right; `--token` does not match the server's `DREAM_TOKEN`.
This is the one failure the sweep can name exactly, which is why it
handshakes rather than trusting an open port.

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
