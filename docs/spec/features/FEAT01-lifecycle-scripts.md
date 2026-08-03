# FEAT01: Lifecycle scripts

## Status

Implementation planned.

## Summary

The `[scripts]` configuration table lets users inject scripts into the project
image and run them at defined points in the container lifecycle.

The existing `post_create` hook will be moved from container creation time into
the project image build. `post_start` and `entrypoint` will remain runtime
scripts.

## Motivation

Projects often need setup steps such as installing tools or dependencies. The
current `post_create` implementation runs after a container starts, storing its
results in the container's ephemeral writable layer. Running it during the image
build makes the setup part of the reusable project image.

## Configuration

The configuration keeps the existing three optional keys:

```toml
[scripts]
post_create = "..."
post_start = "..."
entrypoint = "..."
```

Each value is either:

- **Inline script content**: a TOML multi-line string, detected by containing a
  newline or a `#!` shebang.
- **File reference**: a path relative to the project directory or an absolute
  path. Host environment variables such as `$HOME` are expanded.

### Example

```toml
# .cauldron/cauldron.toml

[scripts]
post_create = ".cauldron/post_create.sh"
post_start = ".cauldron/post-start.sh"
# entrypoint = ".cauldron/entrypoint.sh"  # replaces sleep infinity
```

The `post_create` configuration key is retained for compatibility, although its
execution point is now image build time. Renaming it to `post_build` is outside
the scope of this feature.

## Lifecycle semantics

| Hook | When it runs | Runs as |
|------|--------------|---------|
| `post_create` | Once during the project image build, as the final executable build step. | `cauldron` |
| `post_start` | Every time the container starts or restarts and reaches a running state. | `cauldron` |
| `entrypoint` | Replaces the default `sleep infinity` command as the container's main process. | `cauldron` |

- `post_create` runs during `podman build`, not through `podman exec`.
- `post_create` runs after the image has created and switched to the
  `cauldron` user.
- `post_start` is executed inside the running container with `podman exec`.
- `post_create` is not run again when a container is created from an existing
  image.
- If `entrypoint` is set, the container runs the custom entrypoint instead of
  `sleep infinity`. The user is responsible for keeping the container alive if
  it should remain running.

## Image injection and build plan

Scripts are resolved on the host while preparing the project image build.

For `post_create`, Cauldron will:

1. Write inline content or the referenced file into the temporary build context.
2. Copy it into the image at
   `/usr/local/share/cauldron/post_create.sh`.
3. Make it executable.
4. Set `HOME` to `/home/cauldron`.
5. Switch to the `cauldron` user.
6. Execute it as the final executable build step.

The generated Dockerfile will use `CAULDRON_RUN_POST_CREATE` to enable the
execution. Cauldron sets it to `true` when `post_create` is configured; the
default is `false`.

Conceptually, the final build step is:

```dockerfile
ARG CAULDRON_RUN_POST_CREATE=false
RUN if [ "$CAULDRON_RUN_POST_CREATE" = "true" ]; then \
      /usr/local/share/cauldron/post_create.sh; \
    fi
```

The script remains in the final image after it runs so it can be inspected or
run manually. `post_start` and `entrypoint` continue to be copied into the image
and used at runtime as they are today.

## Execution environment

The `post_create` script must not depend on:

- The project source tree being available in the build context.
- Runtime container mounts.
- SSH agent forwarding.
- Host configuration mounts.
- Runtime-only environment variables.

Support for using project source files during the image build may be added in a
future feature.

## Failure behavior

The image build fails if `post_create` exits with a non-zero status. Cauldron
must report the project image build as failed and must not proceed as though a
usable image was produced.

## Merge behavior

Project `[scripts]` values override global `[scripts]` values at the key level,
matching the merge behavior of the `[env]` table.

## Validation

- A script value must be a string.
- A file reference must resolve to an existing file at build time.
- An unknown hook key raises a warning.

## Rebuild behavior

This feature does not change image selection or invalidation behavior. A change
to `post_create` takes effect when the project image is rebuilt, including with
`cauldron up --build`. Handling stale images or detecting configuration changes
when `--no-build` is used is deferred.

## Testing requirements

Tests must verify that:

- Inline and file-based `post_create` scripts are included in the generated
  Dockerfile.
- The generated Dockerfile sets the build argument and executes the script as
  the `cauldron` user.
- The script execution is the final executable build step.
- The script remains in the image at the documented path.
- A non-zero build result is propagated as an image-build failure.
- `post_create` is not executed through `podman exec`.
- `post_start` and `entrypoint` runtime behavior remains unchanged.

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

This feature covers the three lifecycle hooks above and moving `post_create`
into the image build. It does not cover:

- Renaming `post_create` to `post_build`.
- Copying the project source into the build context for scripts to use.
- Passing runtime mounts, host secrets, or SSH agent access into builds.
- Automatic image invalidation when lifecycle configuration changes.
- Conditional or per-platform scripts.
