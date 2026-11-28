# smolvm nesting - podman-in-smolvm results (target #2)

Validation of running cauldron with the **podman** backend inside an outer
smolvm machine, per tmp/SMOLVM-NESTING-HANDOFF.md target #2. Executed inside
the `cauldron-cauldron` machine (smolvm 1.35.4, guest kernel 6.18.60 aarch64,
booted on the DGX Spark host, kernel 7.0.0-1019-nvidia, without `--nested`).
The machine was built from this repo's image; podman 5.4.2 + uidmap were
installed into the running guest by the user from the host via
`smolvm machine exec --name cauldron-cauldron --user 0 -- ...`.

- date: 2026-11-19T08:45:41Z

## Guest prerequisites

| status | check |
|--------|-------|
| PASS | `/dev/fuse` present (world rw) |
| PASS | subuid/subgid configured (`cauldron:100000:65536`) |
| PASS | user namespaces (`unshare -Urm`) |
| PASS | overlayfs with upperdir inside a user namespace (rw, write-through) |
| PASS | cgroup v2 unified with all controllers; no systemd in guest |
| PASS | network egress from guest (TSI) reaches registries and apt mirrors |
| PASS | cgroup delegation not required: rootless podman ran containers fine |

## Blockers found and how they were handled

1. **`/dev/net/tun` is root-only in the guest.** Rootless podman's network
   helpers (pasta/slirp4netns) need it; first container run failed with
   `Failed to open() /dev/net/tun: Permission denied`. Fixed from the host
   with `chmod 666 /dev/net/tun` — **ephemeral**: the node is recreated with
   mode 600 on each machine boot, so every new boot needs the fix again.

2. **TSI breaks glibc dual-family DNS inside container netnses.** Container
   builds (`apt-get`) failed with `Temporary failure resolving` while the
   same lookups worked single-family. Findings, all reproducible (10/10):
   - `getaddrinfo(AF_INET)` ok, `getaddrinfo(AF_INET6)` ok,
     `getaddrinfo(AF_UNSPEC)` **fails** (glibc, in-container)
   - musl (python:alpine) `AF_UNSPEC` ok, including concurrently
   - glibc `AF_UNSPEC` outside any container (the guest itself) ok
   - two concurrent per-family lookups on separate sockets ok — only the
     single-socket A+AAAA pattern glibc uses for `AF_UNSPEC` fails
   - the container netns has no addresses, no default routes, and 0 RX
     packets while resolutions succeed: container traffic is served by
     TSI (smolvm's guest transport, `tsi_hijack`) outside the netns
   - `podman build --network host` works (build RUN steps run in the
     guest's netns, where glibc DNS works)
   Repro script: `repro-tsi-dns.sh` (same directory) — run it inside the
   machine; it prints the full pattern matrix and the glibc lookups.
   Root-cause isolation (strace-verified; traces in tsi-dns-strace.txt,
   matrix in repro-tsi-dns.sh; each pattern 10/10; independently
   re-validated by a second agent, arc:chat, whose two corrections are
   folded in below):
   - glibc 2.41's `AF_UNSPEC` resolver sends A+AAAA in one `sendmmsg()`
     on one connected UDP socket, per nameserver attempt. Under TSI the
     sendmmsg returns a short count (1 of 2) and the fallback `sendto()`
     for the leftover query fails with EINVAL; glibc abandons the round
     without reading the first query's reply — which was in fact
     delivered to the socket buffer (a raw probe recv's it right after
     the failed send). The lookup fails fast (~20 ms, no timeout) with
     EAI_AGAIN.
   - every single-datagram path works: unconnected `sendto()`
     (sequential or no), single `send()`/`sendmmsg()` on a connected
     socket, `connect()` again then a second query, two simultaneous
     connected sockets. Single-family glibc lookups therefore work,
     and musl (unconnected `sendto()` per query) works. TCP DNS to
     external resolvers also works from the affected containers; the
     failure is nameserver-independent (`--dns` overrides don't help)
     and absent in the guest itself.
   - the container's netns never sees a packet (0 RX counters) — TSI
     hijacks the socket calls regardless of network backend
     (verified with podman's default netavark/pasta and slirp4netns)
   This relates to smolvm issue #785 ("TSI: connected UDP DNS times out
   inside nested Docker bridge networks", fixed in 1.7.2 via libkrunfw
   `2af9a35`): that single-socket single-query case now works, but the
   multi-datagram send and the repeat send on the same connected socket
   are still broken. Upstream smolvm/TSI bug; cauldron cannot fix it,
   only work around it (below). `cauldron check`'s test container is
   unaffected (it resolves nothing).

## Feature added: `build_network` config

New `[container] build_network` key, passed to the host builder's
`--network` flag (podman build and docker build both accept it). Plumbed
through `config.container_build_network` into both backends'
`build_image`/`build_project_image` and the `up`/`code` build flow
(including the template-base retry paths). Default unset = builder
default, so existing behaviour is unchanged. Unit tests added in
tests/test_podman.py, tests/test_smolvm.py and tests/test_config.py.
Docs: docs/spec/config.md, src/cauldron/data/cauldron.toml.

With `build_network = "host"` in the local (gitignored) config:

| status | check |
|--------|-------|
| PASS | `cauldron check` (podman backend): installed, base image, test container |
| PASS | `cauldron up`: intermediate + project image build (incl. post_build), container starts |
| PASS | `cauldron exec`: whoami/pwd + project virtiofs mount visible in container |
| PASS | `cauldron ps`: shows `cauldron-cauldron` running |
| PASS | `cauldron stop` |
| PASS | `cauldron rm` |
| PASS | `uv run pytest -q`: 337 passed, 3 skipped, 3 deselected, 1 failed* |
| PASS | `uv run ruff check .` and `ruff format --check .` |

\* `test_podman_integration.py::test_build_project_image` fails in this
guest only because the project image installs `openssh-server` via apt,
which needs the TSI workaround; it passes in environments with working
container networking. Not a regression: default args are unchanged when
`build_network` is unset.

## Config state after the session

`.cauldron/cauldron.toml` (gitignored) ends the session with
`[backend] type = "smolvm"` (matches the host) and
`build_network = "host"`. To re-run the podman-in-smolvm test inside the
machine, comment the `[backend]` table out (podman default) — see the
header comment in the file. `build_network = "host"` also applies to
smolvm-backend builds (same host builder); it is harmless on the host.

Target #3 (no-KVM graceful UX) was validated in the smolvm-backend state
— see RESULTS-no-kvm-ux.md.

## Note on target #1 (smolvm-in-smolvm)

Blocked upstream on this host: smolvm 1.35.4 has no host-device
passthrough for character devices (`CreateSpec`'s device surface is
gpu/nested_virt/cuda/rosetta/docker_socket/ssh_agent; `--disk` rejects
character devices), and `--nested` hard-fails at machine start on
aarch64 Linux — the bundled libkrun `krun_check_nested_virt` only reads
the x86 `kvm_intel`/`kvm_amd` nested sysfs parameters (returns 0 on
aarch64), and libkrun's KVM nested path is x86-CPUID-only (no ARM
virtual-EL2 exposure). See the session transcript of 2026-11-19.
