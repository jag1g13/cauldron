# FEAT01: Lifecycle scripts

## Status

Implementation planned.

## Summary

The `[scripts]` configuration table lets users inject scripts into the project
image and run them at defined points in the container lifecycle.

The existing `post_create` hook will be renamed to `post_build` and moved from
container creation time into the project image build. `post_start` and
`entrypoint` will remain runtime scripts.

## Motivation

Projects often need setup steps such as installing tools or dependencies. The
current `post_create` implementation runs after a container starts, storing its
results in the container's ephemeral writable layer. Running it during the image
build makes the setup part of the reusable project image.

## Configuration

The configuration keeps the existing three optional keys:

```toml
[scripts]
post_build = "..."
post_start = "..."
entrypoint = "..."
```

Each value is the name of an existing Bash script in either the project's
`.cauldron/` directory or the user's global Cauldron configuration directory,
`~/.config/cauldron/`. Paths outside these directories are not accepted.

### Example

```toml
# .cauldron/cauldron.toml

[scripts]
post_build = "post_build.sh"
post_start = "post-start.sh"
# entrypoint = "entrypoint.sh"  # replaces sleep infinity
```

Script names are resolved from the relevant configuration source. Project
scripts are resolved relative to `.cauldron/`, and global scripts are resolved
relative to `~/.config/cauldron/`. Each referenced file must be a Bash script.

The `post_build` configuration key replaces `post_create`.

## `cauldron init`

`cauldron init` creates a `.cauldron/post_build.sh` template alongside the
existing Dockerfile and configuration templates. The script template contains
a brief documentation comment at the top and starts with:

```bash
#!/bin/bash
# Add commands required to prepare the project image here.
set -euxo pipefail
```

The template is created only when `.cauldron/post_build.sh` does not already
exist. Creating the template does not enable the `post_build` hook; users must
reference `post_build.sh` from the `[scripts]` table explicitly.

## Lifecycle semantics

| Hook | When it runs | Runs as |
|------|--------------|---------|
| `post_build` | Once during the project image build, as the final executable build step. | `cauldron` |
| `post_start` | Every time the container starts or restarts and reaches a running state. | `cauldron` |
| `entrypoint` | Replaces the default `sleep infinity` command as the container's main process. | `cauldron` |

- `post_build` runs during `podman build`, not through `podman exec`.
- `post_build` runs after the image has created and switched to the
  `cauldron` user.
- `post_start` is executed inside the running container with `podman exec`.
- `post_build` is not run again when a container is created from an existing
  image.
- If `entrypoint` is set, the container runs the custom entrypoint instead of
  `sleep infinity`. The user is responsible for keeping the container alive if
  it should remain running.

## Image injection and build plan

Scripts are resolved on the host while preparing the project image build.

For `post_build`, Cauldron will:

1. Look for the named Bash script in the project's `.cauldron/` directory
   first, then fall back to the global `~/.config/cauldron/` directory.
2. Copy script files into the temporary container build context.
3. Copy the selected script into the image at
   `/usr/local/share/cauldron/post_build.sh`.
5. Make it executable.
6. Set `HOME` to `/home/cauldron`.
7. Switch to the `cauldron` user.
8. Execute it as the final executable build step.

The generated Dockerfile will use `CAULDRON_RUN_POST_BUILD` to enable the
execution. Cauldron sets it to `true` when `post_build` is configured; the
default is `false`.

Conceptually, the final build step is:

```dockerfile
ARG CAULDRON_RUN_POST_BUILD=false
RUN if [ "$CAULDRON_RUN_POST_BUILD" = "true" ]; then \
      /usr/local/share/cauldron/post_build.sh; \
    fi
```

The script remains in the final image after it runs so it can be inspected or
run manually. `post_start` and `entrypoint` continue to be copied into the image
and used at runtime as they are today.

## Execution environment

The `post_build` script must not depend on:

- The project source tree being available in the build context.
- Runtime container mounts.
- SSH agent forwarding.
- Host configuration mounts.
- Runtime-only environment variables.

Support for using project source files during the image build may be added in a
future feature.

## Failure behavior

The image build fails if `post_build` exits with a non-zero status. Cauldron
must report the project image build as failed and must not proceed as though a
usable image was produced.

## Merge behavior

Project `[scripts]` values override global `[scripts]` values at the key level,
matching the merge behavior of the `[env]` table.

## Validation

- A script value must be a string.
- A script name must resolve to an existing Bash script in either the project
  `.cauldron/` directory or the global `~/.config/cauldron/` directory at build
  time.
- Paths outside those directories are invalid.
- An unknown hook key raises a warning.

## Rebuild behavior

This feature does not change image selection or invalidation behavior. A change
to `post_build` takes effect when the project image is rebuilt, including with
`cauldron up --build`. Handling stale images or detecting configuration changes
when `--no-build` is used is deferred.

## Testing requirements

Tests must verify that:

- Project and global `post_build` scripts are included in the generated
  Dockerfile.
- The generated Dockerfile sets the build argument and executes the script as
  the `cauldron` user.
- The script execution is the final executable build step.
- The script remains in the image at the documented path.
- A non-zero build result is propagated as an image-build failure.
- `post_build` is not executed through `podman exec`.
- `post_start` and `entrypoint` runtime behavior remains unchanged.

## Future expansion: multiple scripts per hook

A future iteration may allow each hook to be either a single script or a list of
scripts, processed in order:

```toml
[scripts]
post_start = [
  "install-deps.sh",
  "start-services.sh",
]
```

The merge rule would remain simple: the project value replaces the global value,
whether it is a string or a list. This is deferred to keep the initial
implementation minimal.

## Scope

This feature covers the three lifecycle hooks above and moving `post_build`
into the image build. It does not cover:

- Copying the project source into the build context for scripts to use.
- Passing runtime mounts, host secrets, or SSH agent access into builds.
- Automatic image invalidation when lifecycle configuration changes.
- Conditional or per-platform scripts.
