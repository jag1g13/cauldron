# Cauldron Specification

Cauldron is a Python/Click command-line tool that creates and manages containerised development environments using rootless Podman. It is intended to be simpler and more developer-friendly than a full devcontainer workflow while still giving each project an isolated, reproducible environment.

## Goals

- Provide a small, predictable CLI for day-to-day containerised development.
- Use rootless Podman as the only supported container runtime.
- Let developers customise their environment with a personal or per-project Dockerfile.
- Make the host working directory, Git configuration, and SSH agent available inside the container with sensible defaults.
- Work on Linux and macOS initially; WSL2 support is desirable but secondary.

## Non-goals

- Replacing or fully emulating the Dev Containers specification.
- Supporting Docker directly (rootless behaviour is harder to guarantee).
- Multi-container orchestration or production deployment.
- Built-in IDE integration beyond VSCode Remote-Containers.

## Architecture

Cauldron is a thin wrapper around Podman. It does not run a daemon or manage its own state beyond Podman containers and images. All state is derived from:

- The project directory name.
- Podman containers and images.
- Optional configuration files.
- Optional custom Dockerfiles.

```text
host
  │
  ├── cauldron CLI (Python/Click)
  │
  ├── Podman (rootless)
  │     ├── pulls the configured base image
  │     ├── builds project image from base image + user Dockerfile
  │     └── runs container named cauldron-<project-dir>
  │
  └── mounts into container
        ├── current working directory (read/write)
        ├── ~/.gitconfig (read-only)
        └── SSH_AUTH_SOCK (read-only)
```

## Image build flow

1. **Base image**: Cauldron pulls the configured base image (default: `docker.io/library/debian:trixie-slim`). The base image can be overridden in the `[container]` table of the global or project config file.
2. **User customisation**: If a custom Dockerfile exists, Cauldron builds it with the `CAULDRON_BASE` build argument, so the Dockerfile can use `FROM ${CAULDRON_BASE}`. The Dockerfile is searched in this order:
   - `.cauldron/Dockerfile` in the project directory.
   - `~/.config/cauldron/Dockerfile` in the user's home directory.
3. **Project image**: The resulting image is tagged as `cauldron-<project-dir-name>:latest`.

If the project image already exists, it is reused unless `cauldron up --build` is used. If it does not exist, Cauldron builds it automatically on `up` unless `cauldron up --no-build` is used.

## Container lifecycle

- A container is identified by name: `cauldron-<project-dir-name>`.
- The name can be overridden per-command with `--name`.
- `up` creates and starts the container, blocking until it is running. It errors if a container with the chosen name already exists, matching Podman's default behaviour.
- `stop` stops only the container for the current directory (or the one named by `--name`).
- `rm` removes only the stopped container for the current directory. `rm --force` stops a running container first.
- `ps` lists only Cauldron-managed containers.
- `exec` and `code` require a running container; if the target container is not running, they start it first.

## Defaults inside the container

- The container runs rootless with the host user's UID and GID.
- The current working directory is mounted at the same absolute path as on the host.
- `~/.gitconfig` is mounted read-only into the container user's home directory.
- `SSH_AUTH_SOCK` is mounted read-only and the environment variable is passed through.
- The container user's home directory is ephemeral and is discarded when the container is removed.

## Configuration files

Configuration is optional. When present, files are merged with the project file overriding the global file.

- Global config: `~/.config/cauldron/cauldron.toml`
- Project config: `.cauldron/cauldron.toml`

### Environment variables

The `[env]` table sets environment variables inside the container. This is useful for customising the `PATH` of the `cauldron` user or passing other settings without editing a Dockerfile.

```toml
[env]
PATH = "/home/cauldron/.opencode/bin:${PATH}"
EDITOR = "vim"
```

`PATH` values may use `${PATH}` as a placeholder; Cauldron expands it to the image's default `PATH` when starting the container. Other variables are passed through as literal strings.

Project env values override global env values at the key level.

### Base image

The `[container]` table customises the container image.

```toml
[container]
base_image = "astral/uv:python3.14-trixie"
```

The configured image is passed to the build as the `CAULDRON_BASE` build argument. Project values override global values.

### Mounts

The `[container]` table can also declare extra mounts. Mounts may be written as inline tables with `source`, `target`, and optional `options`, or as Docker short-syntax strings such as `"/host:/container:ro,Z"`.

```toml
[container]
mounts = [
  {source = "/home/deck/.aws", target = "/home/cauldron/.aws", options = "ro"},
  "/var/cache:/cache:rw",
]
```

Global and project mounts are merged by `target`. A project mount with the same target as a global mount replaces the global one, and a warning is emitted. If SELinux is enabled on the host, Cauldron warns when a mount's options do not include the `z` or `Z` relabel option.

### Ports

The `[container]` table can publish container ports to the host.

```toml
[container]
ports = [
  "8080:8080",
  "127.0.0.1:3000:3000",
]
```

Global and project ports are merged by host port. A project port that maps the same host port as a global port replaces the global one, and a warning is emitted.

## Platform support

- **Linux**: primary target; rootless Podman is expected to be installed and configured.
- **macOS**: supported via Podman Machine; path and SSH agent forwarding may require additional host setup.
- **Windows / WSL2**: desirable but not a primary target for the initial implementation.

## Security notes

- Cauldron runs containers rootless by default.
- The host working directory is mounted read/write, so a container can modify project files.
- SSH agent and Git configuration are passed through read-only, so the container can use the user's SSH keys and Git identity.
