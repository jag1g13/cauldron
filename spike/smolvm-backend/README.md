# smolvm backend — Phase 0 validation spike

Cauldron's current backend is a thin wrapper around `podman`. We want to add an
opt-in **smolvm** backend (`backend.type = "smolvm"`, podman stays the default).
smolvm runs hardware-virtualized microVMs from OCI images via the `smolvm` CLI,
so the design maps cauldron's "container" concept onto a **named, persistent
smolvm machine**.

This spike exists to confirm, on a real host, the handful of smolvm behaviours
the design depends on *before* we write the backend. It is research-only: it
creates machines, checks behaviour, and cleans up. **No code in the cauldron
source tree is changed.**

## Prerequisites (on the host you run this on)

- Linux with **KVM**: `/dev/kvm` exists (smolvm's local microVMs need it).
  smolvm also runs natively on macOS (Hypervisor.framework) — the same scripts
  work there with minor differences.
- **smolvm** installed and on `PATH` (`smolvm --version`). If your binary is
  named differently (e.g. `smol`), export `SMOLVM_BIN=smol`.
- Outbound **network** (images are pulled from a registry on first boot).
- For the image-handoff check (2.1) only: a host builder, **`podman` or
  `docker`** (we build a tiny marker image, then hand the tar to smolvm).

## How to run

```bash
cd spike/smolvm-backend

./spike.sh              # tiers 0–2 and 4 (non-interactive)
./ssh-test.sh           # tier 3 (VSCode Remote-SSH end-to-end)
```

Options:

- `--keep` — leave the machines running after the run (for manual inspection).
- `SMOLVM_BIN=smol ./spike.sh` — use a differently-named binary.

Everything is safe to re-run. Every machine the spike creates uses the
`cauldron-spike-` name prefix and is removed on exit (unless `--keep`). Only
`/tmp` (and this directory for the result files) are touched.

## What it writes

- `RESULTS.md` — a `PASS / FAIL / BLOCK` summary table. **Send this back.**
- `smolvm-help.txt` — captured `smolvm machine … --help` output (the exact
  subcommands and flags). **Send this back too** — it pins the CLI surface.
- `smolvm-list-sample.txt` — a sample of `machine list` output (for the `ps`
  parser).

Send `RESULTS.md` + `smolvm-help.txt`, and paste the terminal output for any
`FAIL`/`BLOCK` line.

## Tiers and why they matter

| Tier | Check | What it validates for cauldron |
|------|-------|-------------------------------|
| **0** | preflight | smolvm present, `/dev/kvm` present, a builder present; dumps CLI help. |
| **1** | lifecycle | the `up`/`stop`/`start` model: a named machine created from a **Smolfile** boots, is `exec`-able, keeps its **rootfs** across stop+start, and its `init` runs **exactly once**. This is the core of `cauldron up`/`stop`. |
| **2.1** | image handoff | a `podman/docker save` **tar** boots a *named, persistent* machine, both via `--image <tar>` and via a Smolfile `image = "<tar>"`. This is the build→run handoff (smolvm has no builder). |
| **2.2** | `exec -i` | `machine exec -i` forwards stdin — required for the VSCode `sshd -i` ProxyCommand. |
| **2.3** | numeric user | `user = "<uid>:<gid>"` (no `/etc/passwd` entry) runs the workload and a **host bind mount is writable** by that uid — cauldron's same-UID/same-path model. |
| **2.4** | ports | a published port works, and a **host-port conflict** produces a recognizable error (drives `is_port_in_use_error` / `find_port_owner`). |
| **2.5** | list | captures `machine list` JSON shape (to write the `ps` / `find_port_owner` parsers). |
| **3** | Remote-SSH | the full `cauldron code` path: sshd inside the machine, reached through a `ProxyCommand` of `smolvm machine exec … /usr/sbin/sshd -i`. If `ssh … 'id'` succeeds, VSCode Remote-SSH works. |
| **4** | known_hosts | no native smolvm equivalent — confirm we can append to `/etc/hosts` via a root `exec` (cauldron's `known_hosts`). |

## Interpretation

- **PASS** — the behaviour works as the design assumes.
- **FAIL** — the command ran but the result was wrong; the terminal shows the
  raw output. This may force a design tweak (e.g. a different flag or a
  different build strategy).
- **BLOCK** — a prerequisite was missing (no KVM, no builder, binary not
  found), so the check was skipped.

## If a check needs a different flag

smolvm is evolving. If a flag differs (e.g. `machine delete` vs `machine rm`,
or the interactive flag on `exec`), the probe in tier 0 logs what it detected
and `smolvm-help.txt` has the truth. Note it and I'll adjust the backend to the
real CLI rather than re-running the whole spike.
