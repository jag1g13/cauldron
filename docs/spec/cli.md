# Cauldron CLI Specification

Cauldron is invoked as `cauldron [OPTIONS] COMMAND [ARGS...]`. Running `cauldron` without a command prints the command list and brief help.

## Global options

| Option | Description |
|---|---|
| `--help` | Show help message and exit. |
| `--version` | Show the version and exit. |

Options that apply to individual commands (for example `--name`) are listed under each command.

## Commands

| Command | Description |
|---|---|
| [`check`](#check) | Verify dependencies and that Podman can pull, build, and run images. |
| [`up`](#up) | Start or restart the project's container, building the image if needed. |
| [`stop`](#stop) | Stop the project's container. |
| [`rm`](#rm) | Remove the project's container. |
| [`ps`](#ps) | List Cauldron-managed containers. |
| [`exec`](#exec) | Open a shell or run a command in the project's container, starting it if needed. |
| [`code`](#code) | Open the project directory in VSCode Remote-Containers, starting the container if needed. |

## `check`

```text
cauldron check
```

Verifies that the environment is ready to use Cauldron:

1. Podman is installed and runnable (`podman version`).
2. The base image `debian:trixie-slim` can be pulled.
3. The local `cauldron-base:latest` image can be built if needed.
4. A throwaway container from `cauldron-base:latest` can run a simple command (`echo cauldron-check-ok`).

If any step fails, the command exits with a non-zero status and prints the failure reason.

## `up`

```text
cauldron up [OPTIONS]
```

Ensures the project's container is running. If the container does not exist, the image is built (unless `--no-build` is used) and a new container is started. If the container already exists, it is restarted.

### Options

| Option | Description |
|---|---|
| `--name NAME` | Use `NAME` instead of the default `cauldron-<project-dir-name>`. |
| `--build` | Force a rebuild of the project image and recreate the container. |
| `--no-build` | Never build; fail if the project image is missing. |

### Behaviour

- The project image is built from the custom Dockerfile (if any) on top of `cauldron-base:latest`.
- If a container with the chosen name already exists, it is restarted.
- With `--build`, the existing container is removed so it can be recreated from the rebuilt image.
- The current working directory, `~/.gitconfig`, and `SSH_AUTH_SOCK` are mounted as described in the overview.

## `stop`

```text
cauldron stop [OPTIONS]
```

Stops the container for the current project directory.

### Options

| Option | Description |
|---|---|
| `--name NAME` | Stop `NAME` instead of the default project container. |

## `rm`

```text
cauldron rm [OPTIONS]
```

Removes the container for the current project directory.

### Options

| Option | Description |
|---|---|
| `--name NAME` | Remove `NAME` instead of the default project container. |
| `--force` | Stop the container if it is running, then remove it. |

By default, `rm` only removes a stopped container.

## `ps`

```text
cauldron ps [OPTIONS]
```

Lists Cauldron-managed containers. Only containers whose names match Cauldron's naming convention are shown.

### Options

| Option | Description |
|---|---|
| `--all` | Include stopped containers. |

### Output columns

| Column | Description |
|---|---|
| `NAME` | Container name. |
| `IMAGE` | Image used by the container. |
| `STATUS` | Running, exited, etc. |
| `PROJECT` | Host project directory. |

## `exec`

```text
cauldron exec [OPTIONS] [COMMAND] [ARGS...]
```

Runs a command inside the project's container. If the container is not running, it is started first.

### Options

| Option | Description |
|---|---|
| `--name NAME` | Execute in `NAME` instead of the default project container. |

### Behaviour

- If no `COMMAND` is given, an interactive shell is started.
- The shell is taken from the container user's `$SHELL` environment variable, falling back to `bash`.
- The command runs in the mounted project directory.

## `code`

```text
cauldron code [OPTIONS]
```

Opens the current project directory in VSCode using the Remote-Containers extension. If the container is not running, it is started first.

### Options

| Option | Description |
|---|---|
| `--name NAME` | Connect to `NAME` instead of the default project container. |

### Requirements

- The `code` CLI is installed on the host.
- VSCode's Remote-Containers extension is installed.

Cauldron invokes VSCode with a `vscode-remote://attached-container+<container_name>/<workdir>` URI, opening the mounted project directory. The exact URI scheme may be adjusted to match the installed VSCode/Remote-Containers version.

## Exit codes

Cauldron uses Click's default exit codes:

- `0` for success.
- `2` for usage errors (bad arguments, missing options, etc.).
- `1` or another non-zero value for runtime errors.

## Examples

```bash
# Verify the environment
cauldron check

# Start the project container
cauldron up

# Rebuild the image from scratch
cauldron up --build

# Open a shell
cauldron exec

# Run a one-off command
cauldron exec -- make test

# Open the project in VSCode
cauldron code

# Stop and remove the container
cauldron stop
cauldron rm
```
