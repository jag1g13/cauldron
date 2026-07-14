# Cauldron Configuration Specification

Cauldron reads optional TOML configuration files. Project settings override global settings.

- Global config: `~/.config/cauldron/cauldron.toml`
- Project config: `.cauldron/cauldron.toml`

When both files exist, Cauldron merges them. The `[env]` table is merged at the key level, the `[container]` table is merged key-by-key, and `mounts` and `ports` are merged by their natural identifiers so that project values override global values without discarding unrelated entries.

## Top-level sections

### `[container]` — container runtime options

The `[container]` table customises the image and runtime behaviour of the project's container.

#### `base_image`

Override the default Debian base image. The configured image is passed to the build as the `CAULDRON_BASE` build argument, so custom Dockerfiles can use `FROM ${CAULDRON_BASE}`.

```toml
[container]
base_image = "astral/uv:python3.14-trixie"
```

#### `mounts`

Mount additional files or directories into the container. Each entry can be either an inline table or a Docker short-syntax string.

Inline table form:

```toml
[container]
mounts = [
  {source = "/home/deck/.aws", target = "/home/cauldron/.aws", options = "ro"},
  {source = "/var/cache", target = "/cache", options = "rw"},
]
```

Docker short-syntax form:

```toml
[container]
mounts = [
  "/home/deck/.aws:/home/cauldron/.aws:ro",
  "/var/cache:/cache:rw",
]
```

- `source` — the path on the host.
- `target` — the path inside the container.
- `options` — Podman mount options, for example `ro`, `rw`, `Z`, `z`, `rw,Z`. Optional.

Global and project mounts are merged by `target`. If a project mount has the same target as a global one, the project mount wins and Cauldron emits a warning. Duplicates within the same file are also warned about.

When SELinux is installed and enabled on the host, Cauldron warns if a mount's options do not include the `z` or `Z` relabel option. This is a warning, not an error; if the container cannot access the mounted files, add `Z` (private unshared) or `z` (shared) to the options.

#### `ports`

Publish container ports to the host.

```toml
[container]
ports = [
  "8080:8080",
  "127.0.0.1:3000:3000",
  "8080-8090:8080-8090",
]
```

Each entry is a string in Podman's `-p` syntax. Global and project ports are merged by host port. If a project port maps the same host port as a global port, the project port wins and Cauldron emits a warning.

#### Git and SSH agent passthrough

Git configuration and SSH agent forwarding are configured as ordinary mounts and environment variables, not as hard-coded behaviour.

```toml
[container]
mounts = [
  {source = "/home/deck/.gitconfig", target = "/home/cauldron/.gitconfig", options = "ro,Z"},
  {source = "/run/user/1000/keyring/ssh", target = "/run/user/1000/keyring/ssh", options = "ro"},
]

[env]
SSH_AUTH_SOCK = "/run/user/1000/keyring/ssh"
```

`cauldron init` writes a reference `.cauldron/cauldron.toml` with commented examples for these mounts. Uncomment them and adjust the host paths to match your system.

### `[env]` — environment variables

Set environment variables inside the container.

```toml
[env]
PATH = "/home/cauldron/.opencode/bin:${PATH}"
EDITOR = "vim"
PORT = 8080
```

Values are passed as strings. `PATH` values may use `${PATH}` as a placeholder; Cauldron expands it to the image's default `PATH` when starting the container. Other variables are passed through literally. Project env values override global env values at the key level.

## Example project config

```toml
# .cauldron/cauldron.toml

[container]
base_image = "astral/uv:python3.14-trixie"

mounts = [
  {source = "/home/deck/.aws", target = "/home/cauldron/.aws", options = "ro,Z"},
  {source = "/var/cache", target = "/cache", options = "rw"},
]

ports = [
  "8080:8080",
  "127.0.0.1:3000:3000",
]

[env]
PATH = "/home/cauldron/.opencode/bin:${PATH}"
EDITOR = "vim"
```

## Example global config

```toml
# ~/.config/cauldron/cauldron.toml

[container]
mounts = [
  {source = "/home/deck/.ssh", target = "/home/cauldron/.ssh", options = "ro,Z"},
]

ports = [
  "3000:3000",
]

[env]
EDITOR = "nano"
```

With the project config above, the effective configuration is:

- `base_image` from project: `astral/uv:python3.14-trixie`
- Mounts:
  - `/home/deck/.ssh` → `/home/cauldron/.ssh` (global, unless the project also targets `/home/cauldron/.ssh`)
  - `/home/deck/.aws` → `/home/cauldron/.aws` (project)
  - `/var/cache` → `/cache` (project)
- Ports:
  - `8080:8080` (project)
  - `127.0.0.1:3000:3000` (project overrides the global `3000:3000`)
- Environment:
  - `EDITOR = "vim"` (project overrides global)
  - `PATH = "/home/cauldron/.opencode/bin:${PATH}"`

## Validation and error handling

- A mount must have `source` and `target`. Missing keys raise an error when the config is loaded.
- A port must be a string. Non-string port entries raise an error when the config is loaded.
- Overlapping mount targets or host ports are allowed but produce a warning; the last defined value wins.
- Invalid TOML continues to raise the same TOML parsing error as before.

## Scope

This specification covers configuration keys that are currently supported. Cauldron does not support arbitrary Podman run arguments, Docker Compose-style long mount syntax, or env-file loading at this time.
