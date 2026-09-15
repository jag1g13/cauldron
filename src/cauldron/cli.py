import importlib.resources
import pathlib
import sys

import click

from cauldron import config, devcontainers, podman, project, ssh, vscode


def _read_data_file(name):
    """Return the text of a reference file shipped with Cauldron."""
    return importlib.resources.files("cauldron.data").joinpath(name).read_text()


@click.group(invoke_without_command=True)
@click.version_option()
@click.option("--verbose", "-v", is_flag=True, help="Show Podman output in real time.")
@click.pass_context
def cli(ctx, verbose):
    """Cauldron — containerised development environments with rootless Podman."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    podman.set_verbose(verbose)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
def check():
    """Check that dependencies are installed and Podman can pull, build, and run."""
    cfg = config.load_config()
    base_image = config.base_image(cfg)
    steps = [
        ("podman is installed", podman.version),
        ("base image is available", lambda: podman.ensure_base_image(base_image)),
        ("test container can run", lambda: podman.run_test_container(base_image)),
    ]

    for description, step in steps:
        click.echo(f"Checking {description}... ", nl=False)
        if step():
            click.echo("ok")
        else:
            click.echo("failed")
            raise click.ClickException(f"Check failed: {description}")

    click.echo("All checks passed.")


@cli.command()
def init():
    """Create a .cauldron directory with template files for this project."""
    cauldron_dir = pathlib.Path.cwd() / ".cauldron"

    try:
        cauldron_dir.mkdir(exist_ok=True)
    except OSError as exc:
        raise click.ClickException(f"Failed to create {cauldron_dir}: {exc}") from exc

    rel_dir = cauldron_dir.relative_to(pathlib.Path.cwd())

    dockerfile = cauldron_dir / "Dockerfile"
    if dockerfile.exists():
        click.echo(f"Keeping existing {rel_dir / 'Dockerfile'}")
    else:
        dockerfile.write_text(_read_data_file("Dockerfile"))
        click.echo(f"Created {rel_dir / 'Dockerfile'}")

    config_file = cauldron_dir / "cauldron.toml"
    if config_file.exists():
        click.echo(f"Keeping existing {rel_dir / 'cauldron.toml'}")
    else:
        config_file.write_text(_read_data_file("cauldron.toml"))
        click.echo(f"Created {rel_dir / 'cauldron.toml'}")

    post_build = cauldron_dir / "post_build.sh"
    if post_build.exists():
        click.echo(f"Keeping existing {rel_dir / 'post_build.sh'}")
    else:
        post_build.write_text(_read_data_file("post_build.sh"))
        post_build.chmod(0o755)
        click.echo(f"Created {rel_dir / 'post_build.sh'}")


def _tty_flags():
    """Return (interactive, tty) based on the current stdin/stdout."""
    return sys.stdin.isatty(), sys.stdout.isatty()


SCRIPT_INSTALL_PATH = "/usr/local/share/cauldron"


def _run_lifecycle_hook(container, hook, scripts):
    """Run a lifecycle hook script inside the container if configured.

    Returns True if the hook is not configured or exits successfully.
    Streams hook output to the terminal.
    """
    if hook not in scripts:
        return True
    script_path = f"{SCRIPT_INSTALL_PATH}/{hook}.sh"
    click.echo(f"Running {hook} script...")
    return podman.exec_hook_in_container(container, script_path) == 0


def _build_ok(result):
    """Return True if a Podman build result indicates success.

    ``build_project_image`` returns a ``subprocess.CompletedProcess`` so the
    caller can inspect ``stderr``; a plain bool is also accepted.
    """
    if isinstance(result, bool):
        return result
    return result.returncode == 0


def _stderr_suffix(result):
    """Format captured stderr for appending to an error message."""
    stderr = (getattr(result, "stderr", "") or "").strip()
    return f"\n{stderr}" if stderr else ""


def _recover_template_base(result, base_image, project_dir):
    """Recover from a build failure caused by a Dev Container template image.

    Templates (``ghcr.io/devcontainers/templates/...``) use a non-standard
    layer media type that Podman cannot build from, failing with
    ``Unknown media type during manifest conversion``. The equivalent
    pre-built images at ``mcr.microsoft.com/devcontainers/...`` build fine.

    If the failure matches and a matching MCR image can be verified, prompt
    the user to update ``cauldron.toml`` and return the new base image so the
    caller can retry. Returns None (so the caller surfaces the original
    error) when the failure is not a template issue, no replacement can be
    verified, or the user declines.
    """
    stderr = getattr(result, "stderr", "") or ""
    if not (
        devcontainers.is_template_manifest_error(stderr)
        and devcontainers.is_template_image(base_image)
    ):
        return None

    click.echo(
        f"The base image {base_image!r} is a Dev Container template. "
        "Templates are OCI artifacts whose layers Podman cannot build from "
        "directly; use the equivalent pre-built image instead."
    )
    suggestion = devcontainers.resolve_mcr_image(base_image)
    if not suggestion:
        name = devcontainers.template_name(base_image)
        click.echo(
            "Could not verify a matching image (is the registry reachable?). "
            f"Try {devcontainers.MCR_PREFIX}{name}:latest and update "
            "base_image manually, then retry."
        )
        return None
    click.echo(f"Suggested replacement: {suggestion}")
    if click.confirm(
        f"Update cauldron.toml to use {suggestion} and retry the build?",
        default=True,
    ):
        updated = config.set_base_image(project_dir, suggestion)
        if not updated:
            click.echo(
                "No base_image entry found to update; please edit "
                "cauldron.toml manually."
            )
            return None
        click.echo(f"Updated base_image in {updated}.")
        return suggestion
    return None


def _start_project_container(container, build=False, no_build=False, restart=False):
    """Ensure the project's container exists and is running.

    If the container already exists and is stopped, it is started. If it does
    not exist, the image is built (subject to build/no_build flags) and a new
    container is created and started.

    When ``restart`` is true, an existing container is restarted instead of
    being left running. If ``build`` is also true, the existing container is
    removed so it can be recreated from the rebuilt image.

    Configured lifecycle scripts are copied into the image at build time and
    executed at the appropriate lifecycle points.
    """
    if build and no_build:
        raise click.ClickException("--build and --no-build cannot be used together.")

    image = project.project_image_name()
    project_dir = project.project_dir()
    uid, gid = project.host_user()
    cfg = config.load_config(project_dir)
    base_image = config.base_image(cfg)
    scripts = config.container_scripts(cfg)
    has_entrypoint = "entrypoint" in scripts

    if podman.container_exists(container):
        if restart:
            if build:
                click.echo(
                    f"Stopping and removing existing container {container} for rebuild..."
                )
                if podman.container_running(container) and not podman.stop_container(
                    container
                ):
                    raise click.ClickException(f"Failed to stop container {container}.")
                if not podman.remove_container(container):
                    raise click.ClickException(
                        f"Failed to remove container {container}."
                    )
                # Fall through to build and recreate the container.
            else:
                click.echo(f"Restarting container {container}...")
                if not podman.restart_container(container):
                    raise click.ClickException(
                        f"Failed to restart container {container}."
                    )
                if not podman.container_running(container):
                    raise click.ClickException(
                        f"Container {container} did not restart."
                    )
                if not _run_lifecycle_hook(container, "post_start", scripts):
                    raise click.ClickException(
                        f"post_start script failed for {container}."
                    )
                click.echo(f"Container {container} is running.")
                return
        else:
            if podman.container_running(container):
                return
            click.echo(f"Starting existing container {container}...")
            if not podman.start_container(container):
                raise click.ClickException(f"Failed to start container {container}.")
            if not podman.container_running(container):
                raise click.ClickException(f"Container {container} did not start.")
            if not _run_lifecycle_hook(container, "post_start", scripts):
                raise click.ClickException(f"post_start script failed for {container}.")
            click.echo(f"Container {container} is running.")
            return

    if no_build:
        if not podman.image_exists(image):
            raise click.ClickException(
                f"Image '{image}' is missing and --no-build was specified."
            )
    elif build or not podman.image_exists(image):
        click.echo("Building project image...")
        resolved_base = base_image or podman.BASE_IMAGE
        if not podman.ensure_base_image(resolved_base):
            raise click.ClickException("Failed to ensure base image.")

        dockerfile = project.find_dockerfile()
        intermediate = None
        if dockerfile:
            intermediate = f"{image.split(':')[0]}-intermediate:latest"
            click.echo(f"Building intermediate image from {dockerfile}...")
            result = podman.build_image(
                intermediate,
                dockerfile,
                dockerfile.parent,
                build_args={"CAULDRON_BASE": resolved_base},
            )
            if result.returncode != 0:
                new_base = _recover_template_base(result, resolved_base, project_dir)
                if new_base:
                    if not podman.ensure_base_image(new_base):
                        raise click.ClickException("Failed to ensure base image.")
                    click.echo(f"Retrying intermediate build with {new_base}...")
                    result = podman.build_image(
                        intermediate,
                        dockerfile,
                        dockerfile.parent,
                        build_args={"CAULDRON_BASE": new_base},
                    )
                if result.returncode != 0:
                    raise click.ClickException(
                        "Failed to build intermediate image from "
                        f"{dockerfile}.{_stderr_suffix(result)}"
                    )

        project_base = intermediate or resolved_base
        result = podman.build_project_image(
            image,
            uid,
            gid,
            project_base,
            scripts=scripts,
            project_dir=project_dir,
        )
        if not _build_ok(result):
            new_base = _recover_template_base(result, project_base, project_dir)
            if new_base:
                if not podman.ensure_base_image(new_base):
                    raise click.ClickException("Failed to ensure base image.")
                click.echo(f"Retrying project image build with {new_base}...")
                result = podman.build_project_image(
                    image,
                    uid,
                    gid,
                    new_base,
                    scripts=scripts,
                    project_dir=project_dir,
                )
            if not _build_ok(result):
                raise click.ClickException(
                    f"Failed to build project image.{_stderr_suffix(result)}"
                )

    click.echo(f"Starting container {container}...")
    container_env = config.container_env(cfg)
    if not podman.run_container(
        name=container,
        image=image,
        workdir=project_dir,
        project_dir=project_dir,
        uid=uid,
        gid=gid,
        env=container_env,
        mounts=config.container_mounts(cfg),
        ports=config.container_ports(cfg),
        known_hosts=config.container_known_hosts(cfg),
        entrypoint=has_entrypoint,
    ):
        raise click.ClickException(f"Failed to start container {container}.")

    if not podman.container_running(container):
        raise click.ClickException(f"Container {container} did not start.")

    if not _run_lifecycle_hook(container, "post_start", scripts):
        raise click.ClickException(f"post_start script failed for {container}.")

    click.echo(f"Container {container} is running.")


@cli.command()
@click.option("--name", help="Container name (default: cauldron-<project-dir-name>).")
@click.option("--build", is_flag=True, help="Force a rebuild of the project image.")
@click.option(
    "--no-build", is_flag=True, help="Never build; fail if the image is missing."
)
def up(name, build, no_build):
    """Start or restart the project's container, building the image if needed."""
    container = project.container_name(override=name)

    _start_project_container(container, build=build, no_build=no_build, restart=True)


@cli.command()
@click.option("--name", help="Container name (default: cauldron-<project-dir-name>).")
def stop(name):
    """Stop the project's container."""
    container = project.container_name(override=name)

    if not podman.container_exists(container):
        raise click.ClickException(f"Container '{container}' does not exist.")

    click.echo(f"Stopping container {container}...")
    if not podman.stop_container(container):
        raise click.ClickException(f"Failed to stop container {container}.")

    click.echo(f"Container {container} stopped.")


@cli.command()
@click.option("--name", help="Container name (default: cauldron-<project-dir-name>).")
@click.option(
    "--force", is_flag=True, help="Stop a running container before removing it."
)
def rm(name, force):
    """Remove the project's container."""
    container = project.container_name(override=name)

    if not podman.container_exists(container):
        raise click.ClickException(f"Container '{container}' does not exist.")

    if podman.container_running(container) and not force:
        raise click.ClickException(
            f"Container '{container}' is running. "
            "Stop it first or use --force to remove it."
        )

    click.echo(f"Removing container {container}...")
    if not podman.remove_container(container, force=force):
        raise click.ClickException(f"Failed to remove container {container}.")

    click.echo(f"Container {container} removed.")


@cli.command()
@click.option(
    "--all", "all_containers", is_flag=True, help="Include stopped containers."
)
def ps(all_containers):
    """List Cauldron-managed containers."""
    containers = podman.list_containers(all_containers=all_containers)

    if not containers:
        click.echo("No Cauldron containers.")
        return

    name_width = max(len(c["name"]) for c in containers)
    image_width = max(len(c["image"]) for c in containers)
    status_width = max(len(c["status"]) for c in containers)

    header = f"{'NAME':<{name_width}}  {'IMAGE':<{image_width}}  {'STATUS':<{status_width}}  PROJECT"
    click.echo(header)
    for c in containers:
        click.echo(
            f"{c['name']:<{name_width}}  "
            f"{c['image']:<{image_width}}  "
            f"{c['status']:<{status_width}}  "
            f"{c['project_dir']}"
        )


@cli.command("exec", context_settings={"ignore_unknown_options": True})
@click.option("--name", help="Container name (default: cauldron-<project-dir-name>).")
@click.option(
    "--interactive/--no-interactive",
    default=None,
    help="Enable or disable stdin attachment (default: based on stdin).",
)
@click.option(
    "--tty/--no-tty",
    default=None,
    help="Enable or disable pseudo-TTY allocation (default: based on stdout).",
)
@click.argument("command", required=False, default=None)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def exec_command(name, interactive, tty, command, args):
    """Run a command or open a shell in the project's container.

    If the container is not running, it is started first.
    """
    container = project.container_name(override=name)
    _start_project_container(container)

    detected_interactive, detected_tty = _tty_flags()
    if interactive is None:
        interactive = detected_interactive
    if tty is None:
        tty = detected_tty

    if command is None:
        command = podman.container_shell(container)
        args = ()

    exit_code = podman.exec_in_container(
        container, command, args=args, interactive=interactive, tty=tty
    )
    raise SystemExit(exit_code)


@cli.command()
@click.option("--name", help="Container name (default: cauldron-<project-dir-name>).")
def code(name):
    """Open the project directory in VSCode via Remote-SSH."""
    container = project.container_name(override=name)
    _start_project_container(container)

    if not vscode.code_available():
        raise click.ClickException(
            "VSCode CLI ('code') not found. "
            "Make sure VSCode is installed and 'code' is on your PATH."
        )

    try:
        ssh.ensure_keypair()
        ssh.ensure_container_ssh(container)
        ssh.ensure_ssh_config(container)
    except ssh.SSHError as exc:
        raise click.ClickException(str(exc)) from exc

    project_dir = project.project_dir()
    click.echo(f"Opening {project_dir} in VSCode...")
    exit_code = vscode.open_in_code(container, project_dir)
    raise SystemExit(exit_code)
