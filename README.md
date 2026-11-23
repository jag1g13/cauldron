# Cauldron

Cauldron is a developer-friendly command-line tool for creating containerised development environments with rootless Podman. It wraps Podman with a small, predictable CLI and lets you customise your environment using a personal or per-project Dockerfile.

## Features

- Rootless Podman containers, with an opt-in `smolvm` microVM backend.
- Per-project environments identified by directory name.
- Custom Dockerfiles layered on top of the configured base image via the `CAULDRON_BASE` build argument.
- Environment variable customisation via `~/.config/cauldron/cauldron.toml` or `.cauldron/cauldron.toml`.
- Additional mounts, port forwards, and known-hosts entries declared in the config file.
- Commands for checking dependencies, initialising projects, starting/restarting, stopping, removing, listing, executing into, and opening VSCode via Remote-SSH in containers.

## Quick start

```bash
# Verify Podman and image building work
cauldron check

# Create a .cauldron directory with template files for this project
cauldron init

# Start or restart the environment for the current project
cauldron up

# Open a shell
cauldron exec

# Open the project in VSCode (requires Remote-SSH extension)
cauldron code
```

## Configuration

Cauldron reads optional TOML configuration files. Project settings override global settings.

- Global: `~/.config/cauldron/cauldron.toml`
- Project: `.cauldron/cauldron.toml`

### Runtime backend

By default each project runs in a rootless Podman container. An opt-in `smolvm`
backend runs the project in a smol microVM instead:

```toml
[backend]
type = "smolvm"
```

This requires the `smolvm` binary on your `PATH` (override its location with
`CAULDRON_SMOLVM_PATH`) and a host with KVM: `cauldron check` verifies the
device is writable by your user and prints a remedy when it is not (inside
containers, only a named ACL such as
`sudo setfacl -m u:<uid>:rw /dev/kvm` crosses the user namespace; host `kvm`
group membership does not). On hosts whose logind uaccess machinery erases
that ACL on kvm uevents, `scripts/kvm-acl-fix.sh` makes it durable — see
`scripts/README.md`. The project image is still built
with your host builder (`podman` or `docker`) and handed to smolvm as a
`docker save` tar, so custom Dockerfiles and the `CAULDRON_BASE` build argument
work the same way. Podman-only options such as `podman_socket` are ignored under
the smolvm backend. Configured mounts are classified by source path:
directories are shared over virtiofs, Unix sockets (e.g. `$SSH_AUTH_SOCK`)
are passed with `--mount-socket` (no read-only form, so `ro` is ignored),
and plain files or missing paths are skipped with a warning.

Machines can expose nested KVM to the guest, so it can run its own VMs (see
the smolvm-in-smolvm notes in `docs/nested-cauldron.md`):

```toml
[backend.smolvm]
nested = true
```

This passes smolvm's `--nested` on machine creation; the host must support
nested KVM or smolvm fails the start cleanly.

### Base image

Use the `[container]` table to override the default Debian base image. This is useful when a project needs a different distribution or a pre-installed toolchain.

```toml
[container]
base_image = "astral/uv:python3.14-trixie"
```

The image is passed to the build as the `CAULDRON_BASE` build argument, so custom Dockerfiles can use `FROM ${CAULDRON_BASE}`.

### Host Podman socket

To use the host's Podman directly from inside the container — sharing both
images and build cache, without a nested engine — enable `podman_socket`:

```toml
[container]
podman_socket = true
```

On `cauldron up`, Cauldron reads the host's Podman version, downloads a
matching static `podman-remote` client (checksum-verified and cached under
`~/.cache/cauldron/podman-remote/<version>`), mounts it and the host's rootless
socket into the container, and sets `CONTAINER_HOST`. Podman commands in the
 container then run against the host's Podman, so `podman build` reuses the
 host's images and build cache. `cauldron up` also works from inside such a
 container, reusing the host socket and client already mounted in for the new
 sibling container.

 The host must expose its socket:

```bash
systemctl --user enable --now podman.socket
```

Containers created from inside the container are siblings on the host, not
children of the project container. Because they run on the host, the project
directory must exist on the host at the same absolute path (the usual Cauldron
model, where the host project directory is bind-mounted at the same path).
Projects that live only inside the container cannot be bind-mounted by the host
Podman. **Security:** access to the Podman socket is equivalent to full control
of your rootless Podman on the host (it can bind-mount host paths and start
privileged containers), so only enable it where that is acceptable.

### Environment variables

Use the `[env]` table to set environment variables inside the container. This is useful for extending the `PATH` for tools installed by a custom Dockerfile, such as OpenCode installed with its default curl installer.

```toml
[env]
PATH = "/home/cauldron/.opencode/bin:${PATH}"
EDITOR = "vim"
```

`${PATH}` is expanded to the image's default `PATH` when the container starts.

### Lifecycle scripts

Use the `[scripts]` table to inject scripts into the project image that run at
specific points in the container lifecycle:

```toml
[scripts]
post_build = "post_build.sh"
post_start = "post-start.sh"
# entrypoint = "entrypoint.sh"  # replaces sleep infinity
```

- `post_build` runs once as the final executable step of the project image
  build, as the `cauldron` user.
- `post_start` runs every time the container starts or restarts.
- `entrypoint` replaces the default `sleep infinity` process; you are
  responsible for keeping the container alive if needed.

Script names resolve to Bash files in the project's `.cauldron/` directory first,
then the global `~/.config/cauldron/` directory. `cauldron init` creates a
`.cauldron/post_build.sh` template.

### Mounts

Use the `[container]` table to mount additional files or directories into the container. Each mount needs a `source`, `target`, and optional `options`. You can also use Docker's short syntax: `"/host/path:/container/path:ro,Z"`.

```toml
[container]
mounts = [
  {source = "/home/deck/.aws", target = "/home/cauldron/.aws", options = "ro"},
  {source = "/var/cache", target = "/cache", options = "rw"},
]
```

Project mounts override global mounts when they share the same `target`. If SELinux is enabled on the host and a mount does not include the `z` or `Z` option, Cauldron warns you so you can add it.

### Ports

Use the `[container]` table to publish ports from the container to the host.

```toml
[container]
ports = [
  "8080:8080",
  "127.0.0.1:3000:3000",
]
```

Project ports override global ports when they refer to the same host port.

If a container fails to start because a host port is already in use, `cauldron up` names the other Cauldron container (if any) that holds the port, so you know which one to stop or reconfigure.

### Network

Two separate `[container]` keys control networking, each passed straight to the runtime's `--network` flag, and each unset by default (the runtime's own mode — a private bridge with NAT):

- `build_network` — the network used for image **build** steps
- `network` — the network of the **running project container**

Set them to `host` when the default container networking cannot resolve names or reach the network — for example inside a microVM or VM whose nested container networking is degraded, where `host` runs the process in the host's own network namespace (for the guest, that is the VM; with `podman_socket`, the machine hosting the Podman service). The smolvm backend ignores `network` (machines use smolvm's own networking).

```toml
[container]
build_network = "host"
network = "host"
```

In host networking mode published ports (`ports`) are discarded, with a warning from the runtime as the container starts.

### Devices

Use the `[container]` table to pass host device nodes into the container, for example `/dev/kvm`.

```toml
[container]
devices = [
  "/dev/kvm",
]
```

Each entry is passed to Podman's `--device` flag. Use this instead of a mount for device nodes: under rootless Podman a bind-mounted device shows up owned by `nobody:nogroup`, while `--device` hands it to the container user. Project entries override global entries for the same host path.

### Known hosts

Use the `[container]` table to append entries to the container's `/etc/hosts` file for custom domain resolution.

```toml
[container]
known_hosts = [
  "my-service.local:127.0.0.1",
]
```

Each entry is a `hostname:ip` pair passed to Podman's `--add-host` flag. Project entries override global entries for the same hostname.

### Git and SSH agent passthrough

Git configuration and SSH agent forwarding are no longer handled specially by Cauldron. Add them as ordinary mounts and environment variables in your config file. `cauldron init` creates a reference config that includes commented examples for `~/.gitconfig` and `SSH_AUTH_SOCK`.

Host environment variables (e.g. `$HOME`, `$SSH_AUTH_SOCK`) are expanded in mount paths and env values, so you don't need to hard-code paths:

```toml
[container]
mounts = [
  {source = "$HOME/.gitconfig", target = "/home/cauldron/.gitconfig", options = "ro,Z"},
  {source = "$SSH_AUTH_SOCK", target = "$SSH_AUTH_SOCK", options = "ro"},
]

[env]
SSH_AUTH_SOCK = "$SSH_AUTH_SOCK"
```

When a mount target sits under `/run/user/<uid>` (the usual location of `$SSH_AUTH_SOCK`), Podman auto-creates the missing parent directories as root-owned when the container is created. That leaves the container user's `XDG_RUNTIME_DIR` unwritable. Cauldron detects this on every start and restores `/run/user/<uid>` to a user-owned `0700` directory, unless the directory (or an ancestor of it) is a mount point other than a container-local tmpfs, in which case the host-backed mount is left untouched.

## Testing

```bash
# Lint, format check, and run the test suite
uv run ruff check
uv run ruff format --check
uv run pytest
```

Tests run against a real Podman when one is reachable — a local engine, or the
mounted static `podman-remote` when run inside a cauldron — and skip otherwise.
Argument-level assertions use a recording fake executable rather than mocking
Podman in-process. Long-running tests are deselected by default; run them with
`uv run pytest -m slow`. The slow set also drives a real `smolvm` end to end
— machine create/start/exec/stop/delete and the host-port conflict error —
when a real `smolvm` is on `PATH` and `/dev/kvm` is writable by this user
(works both on a host and inside a cauldron), and skips otherwise.

## Documentation

- [Architecture and design](docs/spec/overview.md)
- [CLI reference](docs/spec/cli.md)
- [Configuration reference](docs/spec/config.md)
- [Nested Cauldron: smolvm inside Podman](docs/nested-cauldron.md)

## Status

Cauldron is in early development. Linux and macOS are the initial targets; WSL2 support is desirable but secondary.
