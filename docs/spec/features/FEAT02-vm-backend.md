# FEAT02: Podman Machine backend

## Status

Specification proposed. Implementation not started.

## Summary

Cauldron will support running each project inside a persistent Podman Machine
virtual machine. Podman Machine provides the Linux guest on both Linux and
macOS; the project image and project container continue to be managed by
Podman inside that guest.

The existing Podman backend remains the default. The Podman Machine backend is
selected through global or project configuration. Lima is a potential future
provider, but is not part of this feature.

## Motivation

The current rootless Podman backend shares the host kernel with project
containers. A dedicated virtual machine provides an additional kernel and VM
boundary while retaining the existing Dockerfile, image, container, and
lifecycle-script model.

The backend must work on macOS as well as Linux. Firecracker and Kata
Containers are not initial providers: Firecracker is not a macOS solution, and
Kata is primarily a container-runtime integration rather than a lightweight
per-project VM manager.

## Architecture

The runtime layers are:

```text
host
  +- Cauldron CLI
  +- Podman Machine: cauldron-<project>
       +- Linux guest
       +- Podman
            +- project container: cauldron-<project>
```

The VM and the project container have separate lifecycles:

- The VM supplies the guest kernel, guest filesystem, VM networking, and host
  path sharing.
- Guest Podman builds images and creates, starts, stops, and executes in the
  project container.
- Cauldron does not run project processes directly in the VM outside the
  project container.

The backend uses one persistent VM per project. The VM is retained when the
project is stopped, but is stopped rather than left running.

The current Podman Machine provider supports creating multiple machines but
only one active machine at a time. Cauldron must not silently switch to a
shared VM or weaken the isolation model when another machine is active. If a
different project VM is already running, the command must fail with an
actionable error. Concurrent project VMs are deferred until a provider that
supports them is implemented.

## Configuration

Backend selection is configurable globally and per project. Project values
override global values by key.

```toml
[backend]
type = "podman-machine"

[backend.podman_machine]
# Optional provider-specific settings.
provider = ""
vm_image = ""
cpus = 4
memory = 4096
disk_size = 50
```

- `backend.type` defaults to `podman`.
- `backend.type = "podman"` preserves current behavior.
- `backend.type = "podman-machine"` selects this backend.
- `provider` is passed to `podman machine init --provider` when configured.
- `vm_image` is passed to `podman machine init --image` when configured.
- `cpus`, `memory`, and `disk_size` apply when the VM is created.
- VM settings that Podman Machine cannot change on an existing VM require
  explicit VM recreation; they must not be silently ignored.

The project image base remains a separate setting:

```toml
[container]
base_image = "docker.io/library/debian:trixie-slim"
```

`container.base_image` is an OCI image used by the project Dockerfile.
`backend.podman_machine.vm_image` is a Podman Machine guest image containing
Linux and Podman. The default VM image must provide a supported Podman
installation. A VM image is not a Dockerfile base image and must not be
passed as `CAULDRON_BASE`.

## VM lifecycle

### Creation

On first use, Cauldron will:

1. Check that Podman Machine is available.
2. Create a named machine derived from the project identity.
3. Configure the machine's CPU, memory, disk, provider, and VM image.
4. Configure all required host path shares.
5. Start the machine.
6. Verify that the expected guest Podman connection works.

The machine name must be independent of the project container name where
necessary, and must be persisted through Podman Machine rather than in a
Cauldron-only state file.

### Start and stop

- `cauldron up` starts the project VM if it is stopped, then ensures the
  project container is running.
- `cauldron stop` stops the project container and then stops the VM.
- `cauldron rm` removes the project container but does not remove the VM.
- VM destruction is out of scope for this feature and may be added as an
  explicit future operation.
- A stopped VM may be reused without rebuilding its guest image or pulling
  project images again.

If the VM cannot start because another Podman Machine is active, Cauldron must
report which project appears to own the active machine and explain that it
must be stopped first.

## Image build

All project image operations run through guest Podman:

1. The configured OCI base image is pulled into the guest Podman store.
2. A custom project Dockerfile, if present, is built by guest Podman using the
   existing `CAULDRON_BASE` build argument.
3. The final project image is built by guest Podman, including lifecycle-script
   injection and execution.
4. The project image is tagged using the existing Cauldron project image name.

The build context must be visible at the same guest path used by the build
command. Cauldron must not pass a host-only temporary path to remote Podman.
Generated files may be copied into a guest-visible temporary context or into a
host path already shared with the VM.

Image existence, image IDs, build output, and build failures must refer to the
guest Podman connection, not the host Podman store.

## Host mounts

Every host mount has two layers:

```text
host source
  -> Podman Machine host share
  -> guest source path
  -> project-container bind mount
  -> container target
```

The project directory is handled using the same mechanism as configured
mounts. Where possible, the host and guest source paths retain the same
absolute path so existing configuration remains understandable.

For example:

```text
/home/user/project on the host
  -> /home/user/project in the VM
  -> /home/user/project in the project container
```

The backend must preserve `source`, `target`, and `ro`/`rw` semantics for
ordinary files and directories. Podman-specific SELinux options `z` and `Z`
are not VM mount options and must not be passed to Podman Machine. Cauldron
must document or reject unsupported mount options rather than silently
pretending that both layers accepted them.

Podman Machine host shares are configured when the VM is created. If the
effective project or global mount configuration changes, Cauldron must detect
that the VM configuration is stale and fail with an explicit recreation
message. It must not broaden existing shares automatically without user
confirmation.

The backend must reject host paths that Podman Machine cannot share and report
the failing source path. `podman machine cp` is not a replacement for a live
mount and may only be used for generated build files or other explicitly
temporary data.

## Environment variables

The `[env]` table applies to the inner project container exactly as it does for
the Podman backend. Values are expanded on the host before being passed to
guest Podman.

Environment variables needed to operate the VM or select its Podman
connection are backend implementation details and must not be exposed as
project-container environment variables unless configured in `[env]`.

The existing `${PATH}` expansion uses the project image's default `PATH`, as
reported by guest Podman.

## Lifecycle scripts

Lifecycle scripts retain their existing project-container semantics:

| Hook | Execution location |
|------|--------------------|
| `post_build` | Final executable step of the guest Podman project-image build. |
| `post_start` | `podman exec` in the running project container through the guest connection. |
| `entrypoint` | Entrypoint of the project container. |

The scripts do not run as VM lifecycle hooks. `post_build` cannot depend on
runtime mounts or host-only paths. A failure has the same behavior as in the
Podman backend and prevents Cauldron from treating the image as usable.

## Known hosts

`[container].known_hosts` is passed to guest Podman as inner-container
`--add-host` entries. These entries affect the project container only and do
not modify the VM's `/etc/hosts` or the host's name resolution.

The existing merge behavior by hostname is unchanged.

## Ports

Port publishing has two layers:

```text
host port
  -> Podman Machine forwarding
  -> guest container port
```

The existing `[container].ports` syntax and merge behavior remain unchanged.
The backend must verify the interaction between Podman `-p` and Podman Machine
network forwarding for:

- TCP ports;
- explicit host-address bindings such as `127.0.0.1:3000:3000`;
- port conflicts between projects;
- stopping and restarting the VM;
- any supported UDP behavior.

Port forwarding must fail clearly when the requested host port cannot be
allocated. Cauldron must not claim a port is available based only on guest
Podman state.

## SSH agent forwarding

SSH agent support is required. A host Unix socket must not be treated as
working merely because its path is visible through a VM filesystem share.

The backend must provide a guest-side agent endpoint that forwards SSH agent
protocol requests to the host's configured `SSH_AUTH_SOCK`, then make that
guest endpoint available at the configured path inside the project container.
No private key material may be copied into the VM or project image.

The implementation must define behavior when:

- `SSH_AUTH_SOCK` is unset;
- the host socket disappears after startup;
- the configured socket is not accessible;
- the project container is restarted;
- multiple projects are configured with different agent sockets.

Agent forwarding failures must be reported during container setup or as a
clear runtime error, not silently degraded to an empty socket mount.

## VSCode Remote-SSH

`cauldron code` must continue to connect to the project container's `sshd`
without exposing a public container SSH port.

The backend-specific connection path is conceptually:

```text
VSCode SSH client
  -> Podman Machine SSH connection
  -> guest Podman exec -i
  -> project-container sshd -i
```

The generated SSH configuration must use the selected project's machine and
guest Podman connection. It must never rely on the host Podman default
connection, because that may target a different machine or local runtime.

The existing keypair, authorized-key, host-key, and `cauldron` user behavior is
unchanged.

## Commands and backend abstraction

The CLI commands retain their existing names and user-facing semantics:

- `check` verifies Podman Machine, VM creation/start capability, guest Podman,
  and a throwaway guest container;
- `up`, `stop`, `rm`, `ps`, and `exec` operate on the selected backend;
- `code` uses the selected backend's SSH proxy;
- `--build` rebuilds the project image through guest Podman;
- `--no-build` fails if the project image is absent from guest Podman.

Cauldron should introduce a backend interface for image operations, container
operations, command execution, lifecycle hooks, listing, and SSH proxy
generation. Existing Podman behavior becomes the `podman` implementation;
Podman Machine wraps the same operations against a named guest connection.

## Security

The Podman Machine backend provides an additional VM boundary but does not
make host mounts safe. Code in the project container can still modify every
read/write host path configured for that project.

The VM guest is trusted infrastructure for all containers running in it. A
project container escape could reach the guest kernel, guest Podman, or all
host shares configured for that VM. Therefore:

- one VM is dedicated to one project;
- host Podman sockets must not be mounted into the project container;
- host credentials are shared only when explicitly configured;
- the VM must not be shared between projects in this feature;
- VM and guest Podman images must come from trusted sources;
- the security documentation must not describe this as protection against a
  compromised host kernel or VM implementation.

## Validation and failure behavior

The backend must fail before creating or changing a project container when:

- Podman Machine is unavailable;
- the requested VM image or provider is invalid;
- another Podman Machine is active;
- a required host path cannot be shared;
- the guest Podman connection cannot be established;
- a required host port cannot be forwarded;
- SSH agent forwarding cannot be configured when requested.

Errors should include the project, machine, backend operation, and a useful
remediation where possible.

## Future provider: Lima

Lima is a potential second VM provider. It is not required by this feature and
must not affect the Podman Machine configuration or semantics.

The provider abstraction should allow a future Lima implementation to provide:

- multiple concurrently running project VMs;
- platform-specific VM types such as macOS VZ and Linux QEMU;
- host filesystem sharing and port forwarding;
- guest Podman execution;
- the same SSH agent and VSCode requirements.

Provider-specific differences must remain behind the backend interface. The
configuration should be extensible without making Lima settings mandatory for
Podman Machine users.

## Testing requirements

Tests must verify that:

- global and project backend configuration merges correctly;
- Podman remains the default backend;
- a named Podman Machine is created with the configured VM settings;
- a second active machine is rejected with an actionable error;
- the VM is retained after stop and restarted on the next `up`;
- image builds and image lookups use guest Podman;
- custom Dockerfiles and lifecycle scripts work through guest Podman;
- project and configured host mounts are shared into the VM and then into the
  project container;
- stale or unsupported mount configuration is rejected;
- environment variables and `${PATH}` expansion work in the project container;
- known-host entries reach the project container;
- TCP port forwarding and port conflicts behave as specified;
- SSH agent signing works from inside the project container;
- `cauldron code` reaches the project container through the named VM;
- backend failures do not leave Cauldron believing the project is running.

Provider integration tests may be skipped when Podman Machine or its required
virtualization support is unavailable, but the backend command construction
and state-transition tests must run without a VM.

## Scope

This feature covers:

- the Podman Machine backend;
- one persistent VM per project;
- Linux and macOS host support subject to Podman Machine requirements;
- project image builds through guest Podman;
- mounts, environment variables, lifecycle scripts, known hosts, ports, SSH
  agent forwarding, and VSCode Remote-SSH.

It does not cover:

- concurrent running VMs through Podman Machine;
- multiple projects sharing one VM;
- direct OCI-to-VM image conversion;
- Kata Containers or Firecracker providers;
- Lima implementation;
- automatic VM destruction or snapshot management;
- arbitrary Podman Machine or VM arguments;
- host device passthrough.
