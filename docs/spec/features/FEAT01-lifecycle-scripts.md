# FEAT01: Lifecycle scripts

## Status

Proposed.

## Summary

Introduce a new `[scripts]` config table that lets users inject scripts into the
project image and run them at defined points in the container lifecycle.

## Motivation

Projects often need to perform setup steps that depend on the container
environment, such as installing dependencies, starting background services, or
running migrations. Today this requires either a custom Dockerfile or manual
`cauldron exec` commands. Lifecycle scripts provide a lightweight, config-driven
way to automate these steps.

## Configuration

Add a top-level `[scripts]` table with three optional keys:

```toml
[scripts]
post_create = "..."
post_start = "..."
entrypoint = "..."
```

Each value is either:

- **Inline script content** — a TOML multi-line string. Detected by containing a
  newline or a `#!` shebang.
- **File reference** — a path to a shell script, relative to the project
  directory or absolute. Host environment variables (e.g. `$HOME`) are expanded.

### Example

```toml
# .cauldron/cauldron.toml

[scripts]
post_create = """
#!/bin/bash
set -euo pipefail
pip install -e .
"""

post_start = ".cauldron/post-start.sh"

# entrypoint = ".cauldron/entrypoint.sh"  # replaces sleep infinity
```

## Lifecycle semantics

| Hook | When it runs | Runs as |
|------|--------------|---------|
| `post_create` | After a new container is created and reaches a running state, including after `cauldron up --build` recreates the container. | `cauldron` |
| `post_start` | Every time the container starts or restarts and reaches a running state. | `cauldron` |
| `entrypoint` | Replaces the default `sleep infinity` command as the container's main process. | `cauldron` |

- `post_create` and `post_start` are executed inside the running container with
  `podman exec`.
- `post_create` runs before `post_start` on first creation.
- If `entrypoint` is set, the container runs the custom entrypoint instead of
  `sleep infinity`. The user is responsible for keeping the container alive if
  it should remain running.

## Image injection

Scripts are copied into the project image at build time:

- Inline scripts are written to temporary files and copied to fixed paths inside
  the image, e.g. `/usr/local/share/cauldron/post-create.sh`.
- File references are copied from the resolved host path to the same fixed
  paths.
- All injected scripts are made executable.

The runtime only needs to know the in-image path when executing hooks.

## Merge behaviour

Project `[scripts]` values override global `[scripts]` values at the key level,
matching the merge behaviour of the `[env]` table.

## Validation

- A script value must be a string.
- A file reference must resolve to an existing file at build time.
- An unknown hook key raises a warning.

## Future expansion: multiple scripts per hook

A future iteration may allow each hook to be either a single script or a list of
scripts, processed in order:

```toml
[scripts]
post_start = [
  ".cauldron/install-deps.sh",
  ".cauldron/start-services.sh",
]
```

The merge rule would remain simple: the project value replaces the global value,
whether it is a string or a list. This is deferred to keep the initial
implementation minimal.

## Scope

This feature covers the three lifecycle hooks above. It does not cover:

- Pre-build scripts.
- Scripts that run during image build.
- Conditional or per-platform scripts.
