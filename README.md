# Cauldron

Cauldron is a developer-friendly command-line tool for creating containerised development environments with rootless Podman. It wraps Podman with a small, predictable CLI and lets you customise your environment using a personal or per-project Dockerfile.

## Features

- Rootless Podman containers.
- Per-project environments identified by directory name.
- Custom Dockerfiles layered on top of a Debian base image.
- Sensible defaults: project directory, `~/.gitconfig`, and SSH agent are available inside the container.
- Commands for checking dependencies, starting, stopping, removing, listing, executing into, and opening VSCode in containers.

## Quick start

```bash
# Verify Podman and image building work
cauldron check

# Start the environment for the current project
cauldron up

# Open a shell
cauldron exec

# Open the project in VSCode
cauldron code
```

## Documentation

- [Architecture and design](docs/spec/overview.md)
- [CLI reference](docs/spec/cli.md)

## Status

Cauldron is in early development. Linux and macOS are the initial targets; WSL2 support is desirable but secondary.
