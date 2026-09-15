# Cauldron

Cauldron is a developer-friendly command-line tool for creating containerised development environments with rootless Podman. It wraps Podman with a small, predictable CLI and lets you customise your environment using a personal or per-project Dockerfile.

## Features

- Rootless Podman containers.
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

### Base image

Use the `[container]` table to override the default Debian base image. This is useful when a project needs a different distribution or a pre-installed toolchain.

```toml
[container]
base_image = "astral/uv:python3.14-trixie"
```

The image is passed to the build as the `CAULDRON_BASE` build argument, so custom Dockerfiles can use `FROM ${CAULDRON_BASE}`.

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

## Documentation

- [Architecture and design](docs/spec/overview.md)
- [CLI reference](docs/spec/cli.md)
- [Configuration reference](docs/spec/config.md)

## Status

Cauldron is in early development. Linux and macOS are the initial targets; WSL2 support is desirable but secondary.
