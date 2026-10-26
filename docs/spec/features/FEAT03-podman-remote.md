# FEAT03: Host Podman socket

## Status

Implemented behind the `podman_socket` configuration flag. Validated manually
end to end against a host running Podman 3.4.4, including nested `cauldron up`
run from inside a cauldron.

## Summary

The `[container] podman_socket` option lets a project container use the host's
rootless Podman service through its socket. Cauldron resolves the host's Podman
server version, downloads a static `podman-remote` client that matches it, and
mounts both the client and the socket into the container. Podman commands run in
the container then execute against the host's Podman.

The default is `false`. When disabled, the image, run arguments, and behaviour
are unchanged.

## Motivation

Running a second, nested Podman engine inside the container is fragile: it needs
a privileged container and a working nested user namespace, and builds inside
the nested engine hit overlay and `shadow` locking bugs. Using the host's own
Podman avoids all of that and has two further advantages:

- It shares the host's images and **build cache**, so nothing is pulled or built
  twice.
- The container does not need a Podman engine of its own.

The obstacle is version compatibility: the Podman client shipped in the image
may be newer than the host's server and refuse the connection. Shipping a
matching client removes that constraint without asking the user to upgrade the
host.

## Configuration

```toml
[container]
podman_socket = true
```

The flag is global or per-project and follows the usual merge rules for the
`[container]` table.

## Host requirements

The host must expose its rootless Podman socket:

```bash
systemctl --user enable --now podman.socket
```

The default socket path is `$XDG_RUNTIME_DIR/podman/podman.sock`
(`/run/user/<uid>/podman/podman.sock`). Cauldron fails with an actionable error
if it is missing.

## Client resolution

On container creation Cauldron:

1. Reads the host server version with `podman version --format
   '{{.Server.Version}}'` (falling back to the client version).
2. Downloads the matching static `podman-remote` archive from the upstream
   release, trying the architecture-suffixed asset name
   (`podman-remote-static-linux_<arch>.tar.gz`) before the older
   architecture-neutral name (`podman-remote-static.tar.gz`).
3. Verifies the download against the release's `shasums` file.
4. Extracts the binary and caches it at
   `~/.cache/cauldron/podman-remote/<version>/podman`.

The cached binary is reused on later runs and for later containers using the
same version.

## Container start

When `podman_socket` is enabled, `run_container` mounts:

- the socket directory at `/run/podman-host`;
- the cached static client at `/usr/local/bin/cauldron-podman` (read-only);

and sets:

- `CONTAINER_HOST=unix:///run/podman-host/podman.sock`;
- `CAULDRON_PODMAN_PATH=/usr/local/bin/cauldron-podman`.

`podman.py` resolves its executable from `CAULDRON_PODMAN_PATH`, so the mounted
client is used for every command, including `exec`, `build`, and `run`.

The container is **not** made privileged for this feature. Only the socket and
the client are mounted; the existing `--user ... --userns keep-id` arguments are
retained so the container user matches the host user.

## Host path visibility

Containers created from inside the container are siblings on the host, not
children of the project container. Consequently, every path passed to Podman
(such as the project directory bind mount) is resolved on the **host**. The
project directory must therefore exist on the host at the same absolute path,
which is the normal Cauldron model where the host project directory is
bind-mounted at the same location. Project directories that live only inside the
container cannot be bind-mounted by the host Podman.

## Nested use

`cauldron up` also works when run from inside a cauldron that is itself using the
host socket. The command reaches the host Podman through the mounted static
client, and the new container is a sibling on the host.

The host-side paths are not visible from inside the container (the client is
cached under the host user's home, the socket under the host's runtime
directory), so Cauldron does not re-resolve them. It inspects its own container
mounts to read the exact host socket directory and client the host bind-mounted
in, and reuses those same host paths for the new container. The new sibling is
therefore wired to the same host socket and client as its parent, and `cauldron
up` can be nested to arbitrary depth.

The same applies to the user-configured mounts and environment. Re-expanding
them in-container (e.g. `$HOME`, `$SSH_AUTH_SOCK`) would yield cauldron-local
paths that the host cannot bind-mount, so instead the sibling reuses the
current container's actual host->container mounts (minus the cauldron-managed
project dir, socket, and client) and its host-resource environment variables
(again minus the keys Cauldron manages itself, such as `HOME` and
`CONTAINER_HOST`). The sibling therefore has the same host resource access as
its parent.

The current container id is taken from `CAULDRON_CONTAINER_ID` (an override for
when it cannot be inferred), then the `HOSTNAME` environment variable, then the
system hostname, which Podman sets to the container's id.

## Security

Access to the Podman socket is equivalent to full control of the host user's
rootless Podman: a process in the container can bind-mount host paths and start
privileged containers, and can therefore read and write the host user's files.
The option is off by default and should only be enabled where that trust is
acceptable.

## Testing

- Unit tests cover asset-name selection, version parsing, socket detection,
  checksum/caching behaviour, and the run arguments (socket and client mounts,
  `CONTAINER_HOST`, `CAULDRON_PODMAN_PATH`).
- Argument-level behaviour is asserted through a recording fake executable
  selected via `CAULDRON_PODMAN_PATH`, so no test mocks Podman in-process.
- Integration tests run against whichever Podman the environment provides:
  a local engine on the host, or the mounted static `podman-remote` inside a
  cauldron. This covers image pull/build, container lifecycle, `exec`, and the
  CLI `up`/`exec`/`ps`/`stop`/`rm` flows. They skip cleanly when no service is
  reachable, and test directories are kept inside the project so host Podman can
  bind-mount them when running inside a cauldron.
- Downloading a real static client is covered by a test that is deselected by
  default and run with `pytest -m slow`.
- The nested path is covered by unit tests for the remote detection, container id
  resolution, and host-side path reuse, and by a CLI test asserting the sibling
  container is run with the host's socket and client paths.
- The full flow was validated manually: `cauldron up --build` inside the
  container reused the host's build cache, started a host sibling container, and
  `cauldron exec` succeeded.

## Scope

This feature makes the container use the host's Podman. It does not provide
isolation from the host's Podman state, manage the host socket lifecycle, or
support host Podman versions that lack a published static client. A later,
separate feature (FEAT02) may provide a dedicated per-project VM backend.
