"""Find the dream server on a network whose addresses are not known ahead.

At the venue the relay machine and the machine running the dream server are
joined by a direct ethernet cable with no DHCP, so each end self-assigns a
link-local address (169.254.x.y) picked at random when it boots or when the
cable is replugged. There is nothing to write down in advance, so the relay
goes looking instead: sweep the range for anything accepting connections on
the server's port and let the caller confirm which of those is a dream
server - only the real handshake can tell one from any other service that
happens to sit on port 8000 (see confirm_dream_server in cli.py).

The sweep is one non-blocking connect() per address with a thousand in
flight against a single selector: a thread apiece would want 65k threads,
and one at a time would take hours. An address with nothing behind it costs
the full timeout because nothing answers at all - but a machine that is
there and simply not listening replies RST at once, so the hosts we care
about are never the slow case.
"""

from __future__ import annotations

import errno
import ipaddress
import itertools
import re
import selectors
import socket
import subprocess
import time
from typing import Callable, Iterator, NamedTuple

AUTO = "auto"                   # sweep whatever networks this machine is on
CONNECT_TIMEOUT_S = 0.35        # ample on a LAN; only dead addresses pay it
IN_FLIGHT = 1024                # sockets open at once, fd limit permitting
MAX_ADDRESSES = 70000           # a /16 and no more: bigger means a typo
STALL_LIMIT = 25                # give up if the kernel never finds us room

# connect() on a non-blocking socket answers with one of these when it has
# not finished yet; every other code is a verdict we already have.
_PENDING = frozenset((errno.EINPROGRESS, errno.EALREADY, errno.EWOULDBLOCK))

# ...and these, which mean the machine could not even ask: sweeping a /16
# leaves tens of thousands of unresolved ARP entries behind it and the
# kernel's table has an end. An address we failed to probe is not an empty
# one, so it goes back in the queue rather than being written off. Not
# EAGAIN, which on macOS is the same number as EWOULDBLOCK above: taking it
# for a full table would quietly defer live connections for ever.
_OUT_OF_ROOM = frozenset((errno.ENOBUFS, errno.EADDRNOTAVAIL)) - _PENDING


class Subnet(NamedTuple):
    label: str
    count: int
    addresses: Callable[[], Iterator[str]]
    contains: Callable[[str], bool]
    sample: str  # a plausible address in the range, for the routing check


def parse(pattern: str) -> Subnet:
    """Read `169.254.*.*`, `192.168.1.0/24` or `10.0.1-20.*` as a range.

    Raises ValueError with a readable message; the caller shows it to
    whoever mistyped the argument.
    """
    if "/" in pattern:
        try:
            net = ipaddress.ip_network(pattern, strict=False)
        except ValueError as exc:
            raise ValueError(f"{pattern!r} is not a subnet: {exc}") from exc
        if net.version != 4:
            raise ValueError("only IPv4 subnets can be scanned")
        return Subnet(
            label=str(net),
            count=max(net.num_addresses - 2, 1),
            addresses=lambda: (str(host) for host in net.hosts()),
            contains=lambda ip: ipaddress.ip_address(ip) in net,
            sample=str(next(iter(net.hosts()), net.network_address)),
        )

    parts = pattern.split(".")
    if len(parts) != 4:
        raise ValueError(f"{pattern!r} is not an IPv4 address pattern "
                         "(expected four dot-separated octets)")
    octets: list[range] = []
    try:
        for part in parts:
            if part == "*":
                octets.append(range(0, 256))
            elif "-" in part:
                low, _, high = part.partition("-")
                octets.append(range(int(low), int(high) + 1))
            else:
                octets.append(range(int(part), int(part) + 1))
    except ValueError:
        raise ValueError(f"{pattern!r} has an octet that is neither a "
                         "number, a range (1-20) nor *") from None
    for octet in octets:
        if not octet or octet.start < 0 or octet.stop > 256:
            raise ValueError(f"{pattern!r} has an octet outside 0-255")

    count = 1
    for octet in octets:
        count *= len(octet)
    return Subnet(
        label=pattern,
        count=count,
        addresses=lambda: (".".join(str(n) for n in combo)
                           for combo in itertools.product(*octets)),
        contains=lambda ip: all(
            n in octet for n, octet
            in zip((int(x) for x in ip.split(".")), octets)),
        # .1 rather than .0 where an octet is a wildcard: a real address to
        # ask the routing table about, not a network number
        sample=".".join(str(1 if len(o) == 256 else o.start) for o in octets),
    )


def local_subnets() -> list[Subnet]:
    """Every IPv4 network this machine is attached to, likeliest first.

    The point of asking the machine rather than being told a range: the
    answer is already correct in both worlds. On the venue's direct cable
    the self-assigned address carries a /16 netmask and this returns
    169.254.0.0/16; in the office it returns the ordinary LAN, which sweeps
    in under a second. Loopback is left out (16 million addresses, and the
    relay can be pointed at 127.0.0.1 without looking for it), as is
    anything too large to sweep in a sensible time.

    Link-local goes first even though it is much the slowest to sweep. An
    interface only self-assigns when it has been plugged into something
    with no DHCP on it, which is the venue cable and nothing else; office
    Wi-Fi never produces one. So the cost is only ever paid where it is
    also the right answer, and a dream server that happens to be up on the
    office LAN cannot win a race it should not have been in.

    There is no stdlib way to read a netmask, so we read what a person
    would: ifconfig, or ip on a Linux box without it.
    """
    found: list[tuple[str, ipaddress.IPv4Network]] = []
    for command, inet_re, name_re in (
            # macOS/BSD, one interface per stanza: a header line "en8: flags="
            # then "  inet 192.168.2.2 netmask 0xffffff00 broadcast ...".
            # Requiring the netmask straight after the address skips
            # point-to-point interfaces ("inet A --> B netmask"): a VPN
            # tunnel is not a network with neighbours to go looking for.
            (("ifconfig", "-a"),
             r"^\s+inet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-fA-F]{8})",
             r"^(\S+):"),
            # Linux, one interface per line: "3: eth0    inet 192.168.1.5/24"
            (("ip", "-o", "-4", "addr", "show"),
             r"\sinet (\d+\.\d+\.\d+\.\d+)/(\d+)\s",
             r"^\d+:\s+(\S+)")):
        try:
            output = subprocess.run(command, capture_output=True, text=True,
                                    timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        interface = "?"
        for line in output.splitlines():
            named = re.match(name_re, line)
            if named:
                interface = named.group(1)
            match = re.search(inet_re, line)
            if not match:
                continue
            address, mask = match.groups()
            prefix = (str(int(mask, 16).bit_count()) if mask.startswith("0x")
                      else mask)
            try:
                net = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
            except ValueError:
                continue
            if net.is_loopback or net.num_addresses - 2 > MAX_ADDRESSES:
                continue
            if not any(net == seen for _, seen in found):
                found.append((interface, net))
        if found:
            break

    found.sort(key=lambda pair: (not pair[1].is_link_local,
                                 pair[1].num_addresses))
    return [parse(str(net))._replace(label=f"{net} on {interface}")
            for interface, net in found]


def resolve(pattern: str) -> list[Subnet]:
    """The networks `pattern` asks for: AUTO means the ones we are on."""
    return local_subnets() if pattern == AUTO else [parse(pattern)]


def local_source_address(target: str) -> str | None:
    """Which of this machine's addresses would reach `target`, if any.

    A connected UDP socket sends nothing; it only asks the routing table.
    None means there is no route at all - on a link-local scan that is the
    cable being unplugged, or macOS not having self-assigned yet.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((target, 9))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def _in_flight_limit(want: int) -> int:
    """How many sockets we may hold open at once, raising the cap if we can.

    A stock macOS shell allows 256 descriptors, which would make the sweep
    four times slower than it needs to be; the hard limit is far higher and
    raising the soft one is ours to do.
    """
    try:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft < want + 64:
            resource.setrlimit(resource.RLIMIT_NOFILE,
                               (min(want + 64, hard), hard))
            soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    except Exception:  # noqa: BLE001 - a slower sweep is not a failure
        soft = 256
    return max(64, min(want, soft - 64))


def _sweep(addresses: list[str], port: int,
           timeout: float) -> tuple[list[str], list[str]]:
    """Open every connection in this batch at once; wait once for them all.

    Returns the addresses that accepted, and any we could not start because
    the machine ran out of descriptors - the caller retries those rather
    than reporting an address as empty when we never actually probed it.
    """
    selector = selectors.DefaultSelector()
    started: list[socket.socket] = []
    deferred: list[str] = []
    accepted: list[str] = []

    try:
        for index, address in enumerate(addresses):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            except OSError:                 # out of descriptors
                deferred.extend(addresses[index:])
                break
            started.append(sock)
            sock.setblocking(False)
            error = sock.connect_ex((address, port))
            if error == 0:
                accepted.append(address)       # only ever a local listener
                continue
            if error in _OUT_OF_ROOM:
                deferred.append(address)
                continue
            if error not in _PENDING:
                continue                       # refused, or no route: answered
            selector.register(sock, selectors.EVENT_WRITE, address)

        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break                          # the rest never answered at all
            for key, _ in selector.select(remaining):
                selector.unregister(key.fileobj)
                if key.fileobj.getsockopt(socket.SOL_SOCKET,
                                          socket.SO_ERROR) == 0:
                    accepted.append(key.data)
    finally:
        selector.close()
        for sock in started:
            sock.close()

    return accepted, deferred


def open_ports(subnet: Subnet, port: int, *,
               timeout: float = CONNECT_TIMEOUT_S,
               on_progress: Callable[[int, int], None] | None = None
               ) -> Iterator[str]:
    """Yield every address in `subnet` that accepts a connection on `port`.

    A generator on purpose: the caller confirms each candidate as it turns
    up and can stop the sweep the moment it has the server, which in the
    usual case is long before the range runs out.
    """
    batch_size = _in_flight_limit(IN_FLIGHT)
    remaining = subnet.addresses()
    scanned = 0
    pending: list[str] = []
    stalls = 0

    while True:
        batch = pending + list(itertools.islice(remaining,
                                                batch_size - len(pending)))
        if not batch:
            return
        accepted, pending = _sweep(batch, port, timeout)
        scanned += len(batch) - len(pending)
        if on_progress:
            on_progress(min(scanned, subnet.count), subnet.count)
        yield from accepted

        if len(pending) < len(batch):
            stalls = 0
            continue
        # nothing in that batch could even be attempted: let the sockets we
        # just closed drain out of the kernel before asking again, and stop
        # rather than spin if room never comes back
        stalls += 1
        if stalls > STALL_LIMIT:
            return
        time.sleep(0.2)
