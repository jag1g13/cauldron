# FEAT01: Post-build lifecycle script

## Status

Proposed.

## Summary

Move the existing `post_create` lifecycle script from container creation time to
the project image build. The script will run once as the final build step, after
the image has created and switched to the `cauldron` user.

The configuration key remains `post_create` for now. Its name describes the
existing configuration and lifecycle role, while its execution point changes to
image build time.

## Motivation

The current `post_create` script runs with `podman exec` after a container has
started. This means setup work is repeated for each newly created container and
the resulting files are stored in the container's ephemeral writable layer.

Running the script during the image build makes the setup part of the project
image, so it is available immediately when a container starts and can be reused
by containers created from that image.

## Configuration

The existing `[scripts]` configuration remains unchanged:

```toml
[scripts]
post_create = ".cauldron/post_create.sh"
post_start = ".cauldron/post-start.sh"
# entrypoint = ".cauldron/entrypoint.sh"
```

`post_create` values continue to support:

- Inline script content.
- A file path relative to the project directory.
- An absolute file path, with host environment variables expanded.

`post_start` and `entrypoint` retain their existing runtime behavior.

## Build semantics

When `post_create` is configured, Cauldron will:

1. Resolve the script on the host while preparing the build context.
2. Copy it into the temporary build context.
3. Copy it into the image at:
   `/usr/local/share/cauldron/post_create.sh`.
4. Make it executable.
5. Set `HOME` to `/home/cauldron`.
6. Switch to the `cauldron` user.
7. Run it as the final executable build step.

The generated Dockerfile will use a build argument to enable execution. The
argument is named `CAULDRON_RUN_POST_CREATE` and is set to `true` when the hook
is configured. If no `post_create` hook is configured, it is not executed.

Conceptually, the final build step is:

```dockerfile
ARG CAULDRON_RUN_POST_CREATE=false
RUN if [ "$CAULDRON_RUN_POST_CREATE" = "true" ]; then \
      /usr/local/share/cauldron/post_create.sh; \
    fi
```

The script remains in the final image after it runs so it can be inspected or
run manually.

## Execution environment

The script runs during `podman build` as the image's `cauldron` user, not as
root and not through `podman exec`.

The script must not depend on:

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

## Runtime lifecycle

`post_create` is no longer executed after a container is created. In particular,
creating a container from an existing image must not run it again.

`post_start` continues to run after a container starts or restarts. `entrypoint`
continues to replace the default `sleep infinity` process.

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

## Scope

This feature covers moving the existing `post_create` hook into the project
image build. It does not cover:

- Renaming the configuration key to `post_build`.
- Copying the project source into the build context for scripts to use.
- Passing runtime mounts, host secrets, or SSH agent access into builds.
- Automatic image invalidation when lifecycle configuration changes.
- Changes to `post_start` or `entrypoint` semantics.
