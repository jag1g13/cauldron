# Nested Cauldron: smolvm inside Podman

Notes on the validated topology where a Cauldron dev container (rootless
Podman) hosts smolvm microVMs — "smolvm-in-podman". Validated end to end on
the DGX Spark host; evidence in `spike/smolvm-backend/RESULTS-in-container.md`
(all 18 checks PASS) plus the live dogfood and slow-test runs.

## Topology

    host (KVM)
      └─ rootless Podman container (keep-id; container uid == host user)
           └─ smolvm 1.35.3 boots microVMs on /dev/kvm
                (device passed with [container] devices = ["/dev/kvm"])

smolvm is installed into the project image via `post_build.sh`
(`curl -sSL https://smolmachines.com/install.sh | bash` → `~/.smolvm` +
`~/.local/bin/smolvm`), so no host-path mount is needed.

## Permission crossing

- keep-id maps the container uid onto the host user, so device access is
  granted by a **named ACL** on `/dev/kvm` (`u:<uid>:rw`).
- **Group access never crosses**: the host `kvm` group gid is unmapped in
  the container's user namespace, on this stack supplementary groups are
  dropped, and subordinate ranges do not cover it.
- A plain bind mount of a device node behaves identically to `--device`
  ownership-wise — what matters is the inode's ACL/"other" bits, not how
  the node arrives in the container.

## The uaccess wipe (DGX Spark host)

On this host the kernel emits a uevent for the kvm device when a VM is
*actually created* (opens — even successful ones — and metadata writes do
nothing). Stock udev then re-applies `root:kvm 0660`, and because the node
carries the `uaccess` tag, logind **replaces** its whole ACL with the set of
currently seated users — empty on a headless host — erasing the named ACL
~1 s after the first VM creation. `scripts/kvm-acl-fix.sh` installs a udev
rule that strips the tag from this one node and re-applies the ACL on every
kvm uevent, making the wiping event the restoring one. Details and evidence
in `scripts/README.md`. `cauldron check` (smolvm backend) detects a missing
or unwritable device and prints the remedy.

## Rules that made the nesting work

- Anything the host Podman must resolve (build contexts, mounted sources)
  must live under the workspace bind mount at the **same absolute path on
  both sides**. pytest already keeps its `--basetemp` inside the project
  for this reason.
- Machine state (`~/.cache/smolvm`, `~/.local/share/smolvm`) and the image
  tar cache (`CAULDRON_SMOLTAR_DIR`, default `~/.cache/cauldron/smoltar`)
  stay container-local: they vanish on `cauldron rm`; image-level installs
  belong in `post_build.sh`.
- A machine's **published ports bind in the dev container's netns**, not on
  the physical host. Remote-SSH works (stdio attach via ProxyCommand), but
  a published web port is only reachable from inside the container.
- smolvm 1.35.3 refuses non-interactive deletes without `--force`, even for
  stopped machines — the backend always passes it; scripts must too.

## Validated in-container

- Spike tiers 0-2, 4 all PASS; Remote-SSH tier 3 PASS (end-to-end
  `machine exec --user 0 -i` ProxyCommand).
- Dogfood: `cauldron up` (host build → docker-save tar → microVM) →
  `exec` → `ps` → `stop` → `rm`.
- Real-smolvm slow tests: `uv run pytest -m slow
  tests/test_smolvm_integration.py` — 2 passed.

Next nesting targets — smolvm-in-smolvm (with and without nested KVM) and
Podman-in-smolvm — are tracked in session handovers under `tmp/`.
