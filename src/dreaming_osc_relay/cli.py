"""Standalone Dreaming OSC relay.

Observes a dream server's WebSocket feed (cloud or local - identical
behaviour) and re-emits it as OSC/UDP to local gear, speaking the same
/dreaming/* schema as the backend's in-process --osc mode (the schema
module here is a build-time copy of the backend's canonical one).

    dreaming-osc-relay --server https://<host> --token <token> \
        --osc 127.0.0.1:57120

With a records/ directory beside the package (bundled by build.sh), each
focus change emits full per-work metadata (colour, composition, objects,
faces...); without it the relay still runs, emitting the event stream and
basic work info only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .schema import OscTranslator

RECONNECT_S = 5.0


def load_records(directory: Path) -> dict:
    records: dict = {}
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("._"):
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        records[path.stem] = json.loads(text)
    return records


def default_records_dir() -> Path | None:
    for candidate in (Path.cwd() / "records",
                      Path(__file__).resolve().parents[2] / "records"):
        if candidate.is_dir():
            return candidate
    return None


def ws_url(server: str, token: str | None) -> str:
    base = server.rstrip("/")
    base = base.replace("https://", "wss://").replace("http://", "ws://")
    url = f"{base}/ws/dream?client=osc-relay"
    if token:
        url += f"&token={token}"
    return url


def handle_message(translator: OscTranslator, raw: str) -> str | None:
    message = json.loads(raw)
    event = message.get("event")
    if event == "ping" or message.get("replay"):
        return None  # keepalives / stale catch-up history
    translator.handle(event, message.get("data", {}))
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server",
                        default=os.environ.get("DREAM_SERVER",
                                               "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.environ.get("DREAM_TOKEN"))
    parser.add_argument("--osc", action="append", metavar="HOST:PORT",
                        help="OSC destination (repeatable; default "
                             "127.0.0.1:57120)")
    parser.add_argument("--records", type=Path, default=None,
                        help="artwork records dir (default: bundled)")
    args = parser.parse_args()

    import websockets.sync.client as ws_client

    records_dir = args.records or default_records_dir()
    records = load_records(records_dir) if records_dir else {}
    if not records:
        print("~ no records found: per-work metadata will be minimal "
              "(--records DIR for the full schema)")
    destinations = args.osc or ["127.0.0.1:57120"]
    translator = OscTranslator(records, destinations)
    print(f"~ dreaming-osc-relay: {len(records)} records, emitting to "
          f"{', '.join(destinations)}")

    url = ws_url(args.server, args.token)
    while True:
        try:
            with ws_client.connect(
                    url, additional_headers={
                        "user-agent": "dreaming-osc-relay/0.1"}) as ws:
                print(f"~ observing {args.server}")
                while True:
                    event = handle_message(translator, ws.recv())
                    if event:
                        print(f"  -> {event}")
        except KeyboardInterrupt:
            print("~ relay closed")
            return 0
        except Exception as exc:  # noqa: BLE001 - reconnect forever
            print(f"~ feed lost ({type(exc).__name__}: {exc}); "
                  f"reconnecting in {RECONNECT_S:.0f}s")
            time.sleep(RECONNECT_S)


if __name__ == "__main__":
    sys.exit(main())
