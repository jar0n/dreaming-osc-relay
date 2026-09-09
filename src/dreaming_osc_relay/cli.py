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

The relay follows the server's admin settings: the OSC profile (web schema
or SuperCollider patch), which of the patch's corrections apply, and how
long after a focus change /dreaming/sssh fires. It reads them once on
connect and again on every live `status` event, so a change on the admin
screen reaches the gear on the next work. --profile pins the profile instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .schema import PROFILES, SSSH_DELAY_S, OscTranslator, corrections_enabled

RECONNECT_S = 5.0


class Settings:
    """The server's OSC settings as last seen, read by the translator per
    focus change. `pinned` overrides the profile from the command line."""

    def __init__(self, pinned: str | None = None):
        self.pinned = pinned if pinned in PROFILES else None
        self.profile = self.pinned or "web"
        self.corrections: set | None = None  # None = all of them
        self.sssh_s = SSSH_DELAY_S

    def apply(self, status: dict) -> str:
        """Take the fields a `status` carries; returns a one-line summary."""
        if not isinstance(status, dict):
            return ""
        if self.pinned is None and status.get("osc_profile") in PROFILES:
            self.profile = status["osc_profile"]
        if isinstance(status.get("osc_corrections"), dict):
            self.corrections = corrections_enabled(status["osc_corrections"])
        try:
            if status.get("osc_sssh_s") is not None:
                self.sssh_s = max(0.0, min(30.0, float(status["osc_sssh_s"])))
        except (TypeError, ValueError):
            pass
        return (f"profile {self.profile}{' (pinned)' if self.pinned else ''}, "
                f"sssh after {self.sssh_s:g}s"
                + (f", corrections {','.join(sorted(self.corrections)) or 'none'}"
                   if self.corrections is not None else ""))


def fetch_status(server: str, token: str | None) -> dict:
    """The dreamer's settings from the admin API, or {} when unreachable
    (a server without a resident dreamer, or one that refuses)."""
    import urllib.request

    headers = {"user-agent": "dreaming-osc-relay/0.1"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{server.rstrip('/')}/review/api/dream/status", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except Exception:  # noqa: BLE001 - settings are an enrichment
        return {}


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


def handle_message(translator: OscTranslator, raw: str,
                   settings: Settings | None = None, log=print) -> str | None:
    message = json.loads(raw)
    event = message.get("event")
    if event == "ping" or message.get("replay"):
        return None  # keepalives / stale catch-up history
    if event == "status":
        # the admin changed something: the translator reads the settings
        # per focus change, so this applies from the next work
        if settings is not None:
            summary = settings.apply(message.get("data", {}))
            if summary:
                log(f"~ settings: {summary}")
        return event
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
    parser.add_argument("--profile", choices=PROFILES, default=None,
                        help="pin the OSC profile instead of following the "
                             "server's admin setting (web schema, or the "
                             "SuperCollider patch's rewritten ranges)")
    parser.add_argument("--no-system-trust", action="store_true",
                        help="verify TLS against Python's bundled CA file "
                             "instead of the OS trust store (debugging)")
    args = parser.parse_args()

    if not args.no_system_trust:
        # A managed Mac keeps its network's TLS-inspection root in the
        # Keychain, so browsers accept the re-signed certificate while
        # Python - which reads a static CA *file* - rejects it as
        # "unable to get local issuer certificate". Verifying against the
        # OS store instead makes the relay trust exactly what the machine
        # already trusts. Absent on the venue's plain-ws local server, and
        # a missing truststore must never stop the relay starting.
        try:
            import truststore

            truststore.inject_into_ssl()
        except Exception as exc:  # noqa: BLE001 - TLS is an enrichment here
            print(f"~ system trust store unavailable ({exc}); "
                  "falling back to the bundled CA file")

    import websockets.sync.client as ws_client

    records_dir = args.records or default_records_dir()
    records = load_records(records_dir) if records_dir else {}
    if not records:
        print("~ no records found: per-work metadata will be minimal "
              "(--records DIR for the full schema)")
    destinations = args.osc or ["127.0.0.1:57120"]
    settings = Settings(pinned=args.profile)
    translator = OscTranslator(records, destinations,
                               profile=lambda: settings.profile,
                               corrections=lambda: settings.corrections,
                               sssh_delay=lambda: settings.sssh_s)
    print(f"~ dreaming-osc-relay: {len(records)} records, emitting to "
          f"{', '.join(destinations)}")

    url = ws_url(args.server, args.token)
    while True:
        try:
            summary = settings.apply(fetch_status(args.server, args.token))
            print(f"~ settings: {summary}" if summary
                  else "~ settings: server gave none; using "
                       f"{settings.apply({}) or 'defaults'}")
            with ws_client.connect(
                    url, additional_headers={
                        "user-agent": "dreaming-osc-relay/0.1"}) as ws:
                print(f"~ observing {args.server}")
                while True:
                    event = handle_message(translator, ws.recv(), settings)
                    if event and event != "status":
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
