# Cauldron

Cauldron is a developer-friendly command-line tool for creating containerised development environments with rootless Podman. It wraps Podman with a small, predictable CLI and lets you customise your environment using a personal or per-project Dockerfile.

## Features

- Rootless Podman containers.
- Per-project environments identified by directory name.
- Custom Dockerfiles layered on top of a Debian base image.
- Environment variable customisation via `~/.config/cauldron/cauldron.toml` or `.cauldron/cauldron.toml`.
- Sensible defaults: project directory, `~/.gitconfig`, and SSH agent are available inside the container.
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

Use the `[env]` table to set environment variables inside the container. This is useful for extending the `PATH` for tools installed by a custom Dockerfile, such as OpenCode installed with its default curl installer.

```toml
[env]
PATH = "/home/cauldron/.opencode/bin:${PATH}"
EDITOR = "vim"
```

`${PATH}` is expanded to the image's default `PATH` when the container starts.

## Documentation

- [Architecture and design](docs/spec/overview.md)
- [CLI reference](docs/spec/cli.md)

## Status

Cauldron is in early development. Linux and macOS are the initial targets; WSL2 support is desirable but secondary.
