#!/bin/bash
# Repro: TSI connected-UDP second-send bug in nested container netnses
# (follow-up to smolvm issue #785). Run inside a TSI-backed smolvm machine
# with rootless podman; it spins up one container and probes the UDP
# patterns plus the glibc lookups that depend on them.
#
# Env overrides: IMG (test image), NS (DNS server for the raw UDP probes).
set -u

command -v podman >/dev/null 2>&1 || { echo "need podman in PATH" >&2; exit 1; }
IMG="${IMG:-docker.io/library/python:3.12-slim}"
NS="${NS:-8.8.8.8}"

echo "runtime: podman | image: $IMG | probe nameserver: $NS"

exec podman run -i --rm "$IMG" python - "$NS" <<'PY'
import socket
import struct
import sys
import threading

NS = (sys.argv[1] if len(sys.argv) > 1 else "8.8.8.8", 53)


def query(qtype):
    # standard DNS header + "pypi.org" + the requested type
    return (
        b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
        + b"\x04pypi\x03org\x00"
        + struct.pack("!HH", qtype, 1)
    )


results = []


def check(name, fn):
    try:
        detail = fn()
    except Exception as e:
        results.append(f"FAIL  {name}: {type(e).__name__}: {e}")
    else:
        results.append(f"PASS  {name}: {detail}")


def unconnected_sequential():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)
    s.sendto(query(1), NS)
    first = s.recvfrom(4096)[0]
    s.sendto(query(28), NS)
    second = s.recvfrom(4096)[0]
    return f"responses {len(first)}/{len(second)} bytes"


def connected_single():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)
    s.connect(NS)
    s.send(query(1))
    return f"{len(s.recv(4096))} bytes"


def connected_reconnect():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)
    s.connect(NS)
    s.send(query(1))
    s.recv(4096)
    s.connect(NS)
    s.send(query(28))
    return f"{len(s.recv(4096))} bytes"


def connected_second_send():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)
    s.connect(NS)
    s.send(query(1))
    s.recv(4096)
    # the reported failure: second query, same socket, no reconnect
    s.send(query(28))
    return f"{len(s.recv(4096))} bytes"


def sendmmsg_two_datagrams():
    """glibc's AF_UNSPEC pattern, strace-verified: one connected socket,
    both A and AAAA queries sent in a single sendmmsg() call."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)

    class IOVEC(ctypes.Structure):
        _fields_ = [("iov_base", ctypes.c_void_p), ("iov_len", ctypes.c_size_t)]

    class MSGBASE(ctypes.Structure):
        _fields_ = [
            ("msg_name", ctypes.c_void_p),
            ("msg_namelen", ctypes.c_uint),
            ("msg_iov", ctypes.POINTER(IOVEC)),
            ("msg_iovlen", ctypes.c_size_t),
            ("msg_control", ctypes.c_void_p),
            ("msg_controllen", ctypes.c_size_t),
            ("msg_flags", ctypes.c_int),
        ]

    class MMSGHDR(ctypes.Structure):
        _fields_ = [("msg_hdr", MSGBASE), ("msg_len", ctypes.c_size_t)]

    qa, qb = query(1), query(28)
    buf_a = ctypes.create_string_buffer(qa)
    buf_b = ctypes.create_string_buffer(qb)
    iov_a = (IOVEC * 1)(IOVEC(ctypes.cast(buf_a, ctypes.c_void_p), len(qa)))
    iov_b = (IOVEC * 1)(IOVEC(ctypes.cast(buf_b, ctypes.c_void_p), len(qb)))
    msgs = (MMSGHDR * 2)(
        MMSGHDR(MSGBASE(None, 0, iov_a, 1, None, 0, 0), 0),
        MMSGHDR(MSGBASE(None, 0, iov_b, 1, None, 0, 0), 0),
    )

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)
    s.connect(NS)
    libc.sendmmsg.argtypes = [
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_uint,
    ]
    sent = libc.sendmmsg(s.fileno(), msgs, 2, 0)
    if sent != 2:
        detail = f"sendmmsg sent {sent} of 2"
        try:
            s.send(qb)  # glibc's fallback for the leftover query
        except OSError as e:
            detail += f"; follow-up send of the leftover query: {e!r}"
        # The first query's reply IS delivered to the socket buffer even
        # though the socket refuses anything further; glibc abandons the
        # round after the failed send and never reads it. Say which way
        # this environment behaves.
        try:
            first = s.recv(4096)
            detail += f"; first query's reply was delivered ({len(first)} bytes)"
        except OSError as e:
            detail += f"; first query's reply missing ({e!r})"
        raise RuntimeError(detail)
    first = s.recv(4096)
    second = s.recv(4096)
    return f"responses {len(first)}/{len(second)} bytes"


def simultaneous_connected():
    out = {}

    def go(qtype, key):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(5)
        s.connect(NS)
        s.send(query(qtype))
        out[key] = len(s.recv(4096))

    t1 = threading.Thread(target=go, args=(1, "A"))
    t2 = threading.Thread(target=go, args=(28, "AAAA"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    return f"{out['A']}/{out['AAAA']} bytes"


check("unconnected sequential", unconnected_sequential)
check("connected single", connected_single)
check("connected reconnect", connected_reconnect)
check("sendmmsg 2 datagrams (glibc AF_UNSPEC pattern)", sendmmsg_two_datagrams)
check("connected 2nd send (glibc fallback path)", connected_second_send)
check("simultaneous connected", simultaneous_connected)

for fam, name in [
    (socket.AF_INET, "getaddrinfo AF_INET"),
    (socket.AF_INET6, "getaddrinfo AF_INET6"),
    (0, "getaddrinfo AF_UNSPEC"),
]:
    check(name, lambda f=fam: f"{socket.getaddrinfo('pypi.org', 443, f)[0][4][0]}")

print()
print("\n".join(results))
print()
bug = any(r.startswith("FAIL") and "connected 2nd send" in r for r in results)
unspec = any(r.startswith("FAIL") and "AF_UNSPEC" in r for r in results)
if bug and unspec:
    print("=> bug present (matches the reported failure)")
elif not bug and not unspec:
    print("=> bug not present (all patterns ok)")
else:
    print("=> mixed result, see above")
PY
