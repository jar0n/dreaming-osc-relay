#!/bin/bash
# Why can't the relay reach the dream server? Answers that in one run.
# Needs nothing the relay does not already need. Safe to double-click.
cd "$(dirname "$0")" || exit 1

SERVER="${1:-https://cervh603vyrh85-8000.proxy.runpod.net}"

uv run --quiet python - "$SERVER" <<'PY'
import os
import socket
import ssl
import subprocess
import sys
from urllib.parse import urlparse

url = urlparse(sys.argv[1])
host = url.hostname
port = url.port or (443 if url.scheme == "https" else 80)
print(f"~ checking {host}:{port}\n")

# 1. is the host reachable at all?
try:
    socket.create_connection((host, port), timeout=10).close()
    print(f"[1/4] TCP connect to port {port} ............. OK")
except Exception as exc:
    print(f"[1/4] TCP connect to port {port} ............. FAILED")
    print(f"      {type(exc).__name__}: {exc}\n")
    print("VERDICT: this network will not let you reach the server at all.")
    print("  Nothing to do with the relay. Try a phone hotspot to confirm,")
    print("  then ask IT to allow this host.")
    raise SystemExit(1)

if url.scheme != "https":
    print("\nVERDICT: plain http/ws, no TLS in play. If the relay still")
    print("  fails, the server itself is refusing the connection.")
    raise SystemExit(0)

# 2. who is actually presenting the certificate? Verification is off here on
#    purpose: that is what lets this step succeed and name an interceptor.
lax = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
lax.check_hostname = False
lax.verify_mode = ssl.CERT_NONE
issuer = ""
try:
    with socket.create_connection((host, port), timeout=10) as raw:
        with lax.wrap_socket(raw, server_hostname=host) as tls:
            pem = ssl.DER_cert_to_PEM_cert(tls.getpeercert(binary_form=True))
    shown = subprocess.run(
        ["openssl", "x509", "-noout", "-issuer", "-subject"],
        input=pem, capture_output=True, text=True).stdout.strip()
    print("[2/4] TLS handshake, unverified ............. OK")
    for line in shown.splitlines():
        print(f"      {line}")
    issuer = shown
except Exception as exc:
    print("[2/4] TLS handshake, unverified ............. FAILED")
    print(f"      {type(exc).__name__}: {exc}\n")
    print("VERDICT: the port is open but TLS never completes. Something is")
    print("  interfering with encrypted traffic to this host, almost")
    print("  certainly a filtering proxy on this network.")
    raise SystemExit(1)

# 3. what does this Python trust? `cafile` is the effective path, which
#    SSL_CERT_FILE overrides; openssl_cafile is only the built-in default.
paths = ssl.get_default_verify_paths()
ca = paths.cafile or paths.openssl_cafile
print(f"\n[3/4] Python's CA file ...................... {ca}")
print(f"      exists: {os.path.exists(ca or '')}"
      "   (a FILE, not the macOS Keychain)")
if os.environ.get("SSL_CERT_FILE"):
    print(f"      SSL_CERT_FILE is set: {os.environ['SSL_CERT_FILE']}")
print(f"      roots loaded: {len(ssl.create_default_context().get_ca_certs())}")

# 4. does verification actually pass?
try:
    with socket.create_connection((host, port), timeout=10) as raw:
        with ssl.create_default_context().wrap_socket(
                raw, server_hostname=host):
            pass
    print("[4/4] TLS handshake, verified ............... OK\n")
    print("VERDICT: TLS is fine from here and now. If the relay still drops,")
    print("  the fault is intermittent: most likely a proxy that permits")
    print("  ordinary HTTPS but breaks the WebSocket upgrade. Run the relay")
    print("  again and see whether it reconnects and stays up.")
except Exception as exc:
    print("[4/4] TLS handshake, verified ............... FAILED")
    print(f"      {type(exc).__name__}: {exc}\n")
    if "Google Trust Services" in issuer:
        print("VERDICT: the genuine Runpod certificate is being served, but")
        print("  this Python cannot verify it: its CA file is missing a root")
        print("  your browser has. See TROUBLESHOOTING in the README.")
    else:
        print("VERDICT: this is NOT Runpod's certificate. Something on this")
        print("  network is intercepting TLS and re-signing it as the issuer")
        print("  named above. Your browser accepts it because that issuer is")
        print("  in the macOS Keychain; Python reads a plain file instead and")
        print("  never sees it. See TROUBLESHOOTING in the README.")
    raise SystemExit(1)
PY
