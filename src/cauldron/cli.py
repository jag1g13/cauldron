import os
import pathlib
import sys

import click

from cauldron import config, podman, project, vscode


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
        ("test container can run", podman.run_test_container),
    ]

    for description, step in steps:
        click.echo(f"Checking {description}... ", nl=False)
        if step():
            click.echo("ok")
        else:
            click.echo("failed")
            raise click.ClickException(f"Check failed: {description}")

    click.echo("All checks passed.")


DOCKERFILE_TEMPLATE = """FROM cauldron-base

# Add project-specific packages and tools here.
# These layers are built on top of the Cauldron base image.
"""

CONFIG_TEMPLATE = """# Cauldron project configuration.
#
# Use the [container] table to customise the container image.
# Use the [env] table to set environment variables inside the container.
# PATH values may use ${PATH} as a placeholder for the image's default PATH.

[container]
# base_image = "astral/uv:python3.14-trixie"

[env]
# PATH = "/home/cauldron/.local/bin:${PATH}"
"""


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
        dockerfile.write_text(DOCKERFILE_TEMPLATE)
        click.echo(f"Created {rel_dir / 'Dockerfile'}")

    config_file = cauldron_dir / "cauldron.toml"
    if config_file.exists():
        click.echo(f"Keeping existing {rel_dir / 'cauldron.toml'}")
    else:
        config_file.write_text(CONFIG_TEMPLATE)
        click.echo(f"Created {rel_dir / 'cauldron.toml'}")


def _tty_flags():
    """Return (interactive, tty) based on the current stdin/stdout."""
    return sys.stdin.isatty(), sys.stdout.isatty()


def _start_project_container(container, build=False, no_build=False, restart=False):
    """Ensure the project's container exists and is running.

    If the container already exists and is stopped, it is started. If it does
    not exist, the image is built (subject to build/no_build flags) and a new
    container is created and started.

    When ``restart`` is true, an existing container is restarted instead of
    being left running. If ``build`` is also true, the existing container is
    removed so it can be recreated from the rebuilt image.
    """
    if build and no_build:
        raise click.ClickException("--build and --no-build cannot be used together.")

    image = project.project_image_name()
    project_dir = project.project_dir()
    uid, gid = project.host_user()
    cfg = config.load_config(project_dir)
    base_image = config.base_image(cfg)

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
            click.echo(f"Container {container} is running.")
            return

    if no_build:
        if not podman.image_exists(image):
            raise click.ClickException(
                f"Image '{image}' is missing and --no-build was specified."
            )
    elif build or not podman.image_exists(image):
        click.echo("Building project image...")
        if not podman.ensure_base_image(base_image):
            raise click.ClickException("Failed to ensure base image.")

        dockerfile = project.find_dockerfile()
        intermediate = None
        if dockerfile:
            intermediate = f"{image.split(':')[0]}-intermediate:latest"
            click.echo(f"Building intermediate image from {dockerfile}...")
            if not podman.build_image(intermediate, dockerfile, dockerfile.parent):
                raise click.ClickException(
                    f"Failed to build intermediate image from {dockerfile}."
                )

        if not podman.build_project_image(image, uid, gid, intermediate):
            raise click.ClickException("Failed to build project image.")

    click.echo(f"Starting container {container}...")
    container_env = config.container_env(cfg)
    if not podman.run_container(
        name=container,
        image=image,
        workdir=project_dir,
        project_dir=project_dir,
        uid=uid,
        gid=gid,
        gitconfig=project.gitconfig_path(),
        ssh_auth_sock=project.ssh_auth_sock(),
        env=container_env,
    ):
        raise click.ClickException(f"Failed to start container {container}.")

    if not podman.container_running(container):
        raise click.ClickException(f"Container {container} did not start.")

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
@click.argument("command", required=False, default=None)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def exec_command(name, command, args):
    """Run a command or open a shell in the project's container.

    If the container is not running, it is started first.
    """
    container = project.container_name(override=name)
    _start_project_container(container)

    interactive, tty = _tty_flags()

    if command is None:
        command = os.environ.get("SHELL", "/bin/bash")
        args = ()

    return podman.exec_in_container(
        container, command, args=args, interactive=interactive, tty=tty
    )


@cli.command()
@click.option("--name", help="Container name (default: cauldron-<project-dir-name>).")
def code(name):
    """Open the project directory in VSCode Remote-Containers."""
    container = project.container_name(override=name)
    _start_project_container(container)

    if not vscode.code_available():
        raise click.ClickException(
            "VSCode CLI ('code') not found. "
            "Make sure VSCode is installed and 'code' is on your PATH."
        )

    project_dir = project.project_dir()
    click.echo(f"Opening {project_dir} in VSCode...")
    return vscode.open_in_code(container, project_dir)
