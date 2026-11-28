# smolvm mount classification - live validation results (issue #15)

Validation of the mount-classification fix (sockets via `--mount-socket`,
directories via virtiofs `-v`, plain files / missing paths skipped with a
warning), executed against the live `cauldron-cauldron` machine (smolvm
1.35.4, aarch64, guest kernel 6.18.60). The machine was recreated at boot
(2026-11-23 09:25 UTC) from the local project config, which now carries the
socket mount:

```toml
[backend]
type = "smolvm"

[container]
mounts = [
  {source = "$SSH_AUTH_SOCK", target = "$SSH_AUTH_SOCK", options = "ro"},
  {source = "$HOME/.config/opencode", target = "/home/cauldron/.config/opencode", options = "ro,Z"},
]

[env]
SSH_AUTH_SOCK = "$SSH_AUTH_SOCK"
```

Host agent socket: `/home/k2256813/.ssh/agent.sock` (user re-loaded the
signing key into the host agent after reboot).

- date: 2026-11-23T09:45:00Z

| status | check |
|--------|-------|
| PASS | `run_container` classified the socket mount: the guest has a bind mount at `/home/k2256813/.ssh/agent.sock` whose source is smolvm's published socket (`/proc/self/mountinfo`: `0:31 /run/smolvm/published-sockets/6100.sock /home/k2256813/.ssh/agent.sock`). A host unix socket can only reach the guest via `--mount-socket` (virtiofs carries directories only), so the create call included `--mount-socket /home/k2256813/.ssh/agent.sock:/home/k2256813/.ssh/agent.sock` |
| PASS | smolvm created the guest parent path for the socket target: `/home/k2256813/.ssh/` exists in the guest, root:root 755, created at boot time by the guest init (the `/run/user/1000` concern from the handoff did not materialise for a `/home` target) |
| PASS | socket is functional from the guest: `ssh-add -l` → `256 SHA256:N85ofpiDQiwno03q/Y71uG59u+ym72YPtWTdSwlj3Pc k2256813@k2256813-ubuntu (ED25519)` (the host key); `SSH_AUTH_SOCK` env var set in the guest points at the mounted path |
| PASS | signing round-trip through the mounted socket: commit `f9eb675` (on `feat/smolvm-backend`) was signed from inside the guest via the host agent; the signature was verified in-guest with `ssh-keygen -Y find-principals` against the agent public key (principal matched) |
| PASS | `ro` dropped for the socket, as designed: the mounted socket is `srwxrwxrwx` (fully writable); smolvm has no read-only socket form |
| PASS | directory mount rw (Step 8B): repo at `/home/k2256813/tools/cauldron` (virtiofs `smolvm0`, rw); writes made in-guest (the commit above) are visible on the host |
| PASS | directory mount ro (Step 8B): `/home/cauldron/.config/opencode` (virtiofs `smolvm1`, ro); `touch` in-guest fails with `Read-only file system`, rc 1 |
| PASS | Step 9 missing-binary, `CAULDRON_SMOLVM_PATH=/nonexistent cauldron check`: `Checking smolvm is installed... failed` / `Error: Check failed: smolvm is installed`, exit 1, no traceback |
| PASS | Step 9 missing-binary, `CAULDRON_SMOLVM_PATH=/nonexistent cauldron up`: clean `Error: Failed to ensure base image.`, exit 1, no traceback (see note below on which failure surfaces) |

Notes:
- The warn-and-skip path (plain files, e.g. `~/.gitconfig`) is unit-tested
  only; the live config has that mount commented out, so it was not
  exercised in this run.
- `up` with the smolvm binary missing fails at `ensure_base_image` when the
  host builder is also absent (as in this guest), so the smolvm-missing
  error is masked by the builder error there; on the KVM host (podman
  present) `up` builds first and then fails at `machine create` with
  smolvm's stderr. Either way the failure is a `ClickException` — no
  traceback — because `smolvm._run` maps `FileNotFoundError` to returncode
  1.
- Security note: the mounted socket is world-writable in the guest, so a
  guest process can add or remove keys in the host agent. This is inherent
  to smolvm's `--mount-socket` (no ro form); acceptable for the dev-VM use
  case, worth knowing.
- Guest-local `smolvm` only sees its own (empty) state: `smolvm machine ls`
  in the guest reports no machines, so machine lifecycle commands must run
  on the host.
