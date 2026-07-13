import click

from cauldron import podman, project


@click.group(invoke_without_command=True)
@click.version_option()
@click.pass_context
def cli(ctx):
    """Cauldron — containerised development environments with rootless Podman."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
def check():
    """Check that dependencies are installed and Podman can pull, build, and run."""
    steps = [
        ("podman is installed", podman.version),
        ("base image is available", podman.ensure_base_image),
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


@cli.command()
@click.option("--name", help="Container name (default: cauldron-<project-dir-name>).")
@click.option("--build", is_flag=True, help="Force a rebuild of the project image.")
@click.option(
    "--no-build", is_flag=True, help="Never build; fail if the image is missing."
)
def up(name, build, no_build):
    """Build if necessary and start the project's container."""
    if build and no_build:
        raise click.ClickException("--build and --no-build cannot be used together.")

    container = project.container_name(override=name)
    image = project.project_image_name()
    project_dir = project.project_dir()
    uid, gid = project.host_user()

    if podman.container_exists(container):
        raise click.ClickException(
            f"Container '{container}' already exists. "
            "Remove it with 'cauldron rm' before running 'up'."
        )

    if no_build:
        if not podman.image_exists(image):
            raise click.ClickException(
                f"Image '{image}' is missing and --no-build was specified."
            )
    elif build or not podman.image_exists(image):
        click.echo("Building project image...")
        if not podman.ensure_base_image():
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
    if not podman.run_container(
        name=container,
        image=image,
        workdir=project_dir,
        project_dir=project_dir,
        uid=uid,
        gid=gid,
        gitconfig=project.gitconfig_path(),
        ssh_auth_sock=project.ssh_auth_sock(),
    ):
        raise click.ClickException(f"Failed to start container {container}.")

    if not podman.container_running(container):
        raise click.ClickException(f"Container {container} did not start.")

    click.echo(f"Container {container} is running.")


@cli.command()
@click.option("--name", help="Container name (default: cauldron-<project-dir-name>).")
def stop(name):
    """Stop the project's container."""
    raise NotImplementedError("stop is not yet implemented")


@cli.command()
@click.option("--name", help="Container name (default: cauldron-<project-dir-name>).")
@click.option(
    "--force", is_flag=True, help="Stop a running container before removing it."
)
def rm(name, force):
    """Remove the project's container."""
    raise NotImplementedError("rm is not yet implemented")


@cli.command()
@click.option(
    "--all", "all_containers", is_flag=True, help="Include stopped containers."
)
def ps(all_containers):
    """List Cauldron-managed containers."""
    raise NotImplementedError("ps is not yet implemented")


@cli.command(context_settings={"ignore_unknown_options": True})
@click.option("--name", help="Container name (default: cauldron-<project-dir-name>).")
@click.argument("command", required=False, default=None)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def exec(name, command, args):
    """Run a command or open a shell in the project's container."""
    raise NotImplementedError("exec is not yet implemented")


@cli.command()
@click.option("--name", help="Container name (default: cauldron-<project-dir-name>).")
def code(name):
    """Open the project directory in VSCode Remote-Containers."""
    raise NotImplementedError("code is not yet implemented")
