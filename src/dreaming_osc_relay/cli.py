"""Standalone Dreaming OSC relay.

Observes a dream server's WebSocket feed (cloud or local - identical
behaviour) and re-emits it as OSC/UDP to local gear, speaking the same
/dreaming/* schema as the backend's in-process --osc mode (the schema
module here is a build-time copy of the backend's canonical one).

    dreaming-osc-relay --server https://<host> --token <token> \
        --osc 127.0.0.1:57120

At the venue there is no address to be told: the direct cable has no DHCP,
so both ends self-assign a different 169.254.x.y every time they boot.
--discover sweeps this machine's own networks for the server instead.

    dreaming-osc-relay --discover --token <token>

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

from . import discover
from .schema import OscTranslator

RECONNECT_S = 5.0
USER_AGENT = "dreaming-osc-relay/0.1"
DREAM_PORT = 8000        # where a dream server listens, venue or cloud
REDISCOVER_AFTER = 3     # failed reconnects before the address is suspect


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


def confirm_dream_server(server: str, token: str | None,
                         timeout: float = 4.0) -> str:
    """Is there a dream server at `server`? "yes", "token" or "no".

    Nothing short of the real handshake can say. Port 8000 is a popular
    port, and a relay that settled for "something answered" would sooner or
    later attach itself to an unrelated dev server and fail later, somewhere
    less obvious than here.
    """
    import websockets.sync.client as ws_client
    from websockets.exceptions import InvalidStatus

    try:
        with ws_client.connect(ws_url(server, token), open_timeout=timeout,
                               close_timeout=1.0, additional_headers={
                                   "user-agent": USER_AGENT}):
            return "yes"
    except InvalidStatus as exc:
        # it talked HTTP and turned the upgrade down: 401/403 is a dream
        # server objecting to the token, anything else is not one at all
        return "token" if exc.response.status_code in (401, 403) else "no"
    except Exception:  # noqa: BLE001 - any other failure just means "not it"
        return "no"


def discover_server(pattern: str, token: str | None) -> str | None:
    """Sweep for a dream server, returning its URL or None.

    `pattern` is resolved to networks afresh on every sweep rather than once
    at startup: between one sweep and the next, the cable may have been
    plugged in - and with it the only network the server was ever going to
    be on may have come into existence.
    """

    # A live counter is worth a lot during a 30-second sweep, but only to
    # someone watching: redirected to a log, \r just makes a mess, so there
    # it becomes an occasional ordinary line instead.
    live = sys.stdout.isatty()

    def progress(done: int, total: int) -> None:
        if live:
            print(f"\r  scanned {done}/{total}", end="", flush=True)
        elif done % 16384 < 1024 or done == total:
            print(f"  scanned {done}/{total}", flush=True)

    def clear() -> None:  # wipe the in-place progress line
        if live:
            print("\r" + " " * 48 + "\r", end="", flush=True)

    subnets = discover.resolve(pattern)
    if not subnets:
        print("~ this machine is on no network worth sweeping - nothing but "
              "loopback is up. Is the cable in?")
        return None

    refused: list[str] = []
    for subnet in subnets:
        print(f"~ sweeping {subnet.label} for a dream server on port "
              f"{DREAM_PORT} ({subnet.count} addresses)")

        source = discover.local_source_address(subnet.sample)
        if source is None:
            print("  no route to that network from here: the cable is out, "
                  "or no address has been self-assigned yet")
            continue
        if subnet.contains(source):
            print(f"  this machine is {source}")
        else:
            print(f"  warning: this machine has no address on "
                  f"{subnet.label} (traffic would leave from {source} "
                  "instead), so this will most likely find nothing")

        for host in discover.open_ports(subnet, DREAM_PORT,
                                        on_progress=progress):
            server = f"http://{host}:{DREAM_PORT}"
            clear()
            print(f"  {host} is listening ... ", end="", flush=True)
            verdict = confirm_dream_server(server, token)
            print({"yes": "the dream server",
                   "token": "a dream server, but it refused the token",
                   "no": "not a dream server"}[verdict])
            if verdict == "yes":
                return server
            if verdict == "token":
                refused.append(server)
        clear()

    if refused:
        print(f"~ {refused[0]} is a dream server but rejected the token: "
              "the address was found, --token is what is wrong")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=None,
                        help="dream server URL (default $DREAM_SERVER, else "
                             "http://127.0.0.1:8000)")
    parser.add_argument("--discover", nargs="?", metavar="SUBNET",
                        const=discover.AUTO,
                        help=f"find the server instead of being told where "
                             f"it is: sweep for one answering on port "
                             f"{DREAM_PORT}. Bare --discover sweeps whatever "
                             f"networks this machine is on, which at the "
                             f"venue is the direct cable's 169.254.0.0/16; "
                             f"or name one (169.254.*.*, 192.168.1.0/24)")
    parser.add_argument("--token", default=os.environ.get("DREAM_TOKEN"))
    parser.add_argument("--osc", action="append", metavar="HOST:PORT",
                        help="OSC destination (repeatable; default "
                             "127.0.0.1:57120)")
    parser.add_argument("--records", type=Path, default=None,
                        help="artwork records dir (default: bundled)")
    parser.add_argument("--no-system-trust", action="store_true",
                        help="verify TLS against Python's bundled CA file "
                             "instead of the OS trust store (debugging)")
    args = parser.parse_args()

    # Redirected to a log, print() would sit in an 8k buffer - which is a
    # whole run of the relay, and now also a sweep that can take half a
    # minute before it says anything. A terminal gets this for free.
    sys.stdout.reconfigure(line_buffering=True)

    if args.discover and args.discover != discover.AUTO:
        # fail on a mistyped range now, not after the records have loaded
        try:
            named = discover.parse(args.discover)
        except ValueError as exc:
            parser.error(str(exc))
        if named.count > discover.MAX_ADDRESSES:
            parser.error(f"{named.label} is {named.count} addresses; "
                         f"sweeping more than {discover.MAX_ADDRESSES} would "
                         "take hours. Narrow the range")

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
    translator = OscTranslator(records, destinations)
    print(f"~ dreaming-osc-relay: {len(records)} records, emitting to "
          f"{', '.join(destinations)}")

    server = args.server or os.environ.get("DREAM_SERVER")
    if args.discover:
        if server:
            print(f"~ --discover given, so {server} is ignored")
        server = None  # swept for below, and again if it ever goes stale
    else:
        server = server or "http://127.0.0.1:8000"

    failures = 0
    while True:
        try:
            if server is None:
                server = discover_server(args.discover, args.token)
                if server is None:
                    print("~ no dream server found; sweeping again in "
                          f"{RECONNECT_S:.0f}s")
                    time.sleep(RECONNECT_S)
                    continue
            with ws_client.connect(
                    ws_url(server, args.token), additional_headers={
                        "user-agent": USER_AGENT}) as ws:
                print(f"~ observing {server}")
                failures = 0
                while True:
                    event = handle_message(translator, ws.recv())
                    if event:
                        print(f"  -> {event}")
        except KeyboardInterrupt:
            print("~ relay closed")
            return 0
        except Exception as exc:  # noqa: BLE001 - reconnect forever
            failures += 1
            print(f"~ feed lost ({type(exc).__name__}: {exc}); "
                  f"reconnecting in {RECONNECT_S:.0f}s")
            if args.discover and failures >= REDISCOVER_AFTER:
                # a self-assigned address does not survive the other end
                # rebooting or the cable being replugged, and reconnecting
                # forever to an address nothing answers on is the one way
                # this relay can look alive while being permanently deaf
                print(f"~ {server} has failed {failures} times; it may have "
                      "moved. Sweeping for it again")
                server = None
                failures = 0
            time.sleep(RECONNECT_S)


if __name__ == "__main__":
    sys.exit(main())
