# Cauldron

Cauldron is a developer-friendly command-line tool for creating containerised development environments with rootless Podman. It wraps Podman with a small, predictable CLI and lets you customise your environment using a personal or per-project Dockerfile.

## Features

- Rootless Podman containers.
- Per-project environments identified by directory name.
- Custom Dockerfiles layered on top of the configured base image via the `CAULDRON_BASE` build argument.
- Environment variable customisation via `~/.config/cauldron/cauldron.toml` or `.cauldron/cauldron.toml`.
- Additional mounts and port forwards declared in the config file.
- Commands for checking dependencies, initialising projects, starting/restarting, stopping, removing, listing, executing into, and opening VSCode in containers.

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

# Open the project in VSCode
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
