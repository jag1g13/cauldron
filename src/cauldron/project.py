import os
import pathlib


DEFAULT_CONTAINER_PREFIX = "cauldron"
DOCKERFILE_PROJECT_PATH = pathlib.Path(".cauldron") / "Dockerfile"
DOCKERFILE_GLOBAL_PATH = pathlib.Path.home() / ".config" / "cauldron" / "Dockerfile"
CONFIG_PROJECT_PATH = pathlib.Path(".cauldron") / "cauldron.toml"
CONFIG_GLOBAL_PATH = pathlib.Path.home() / ".config" / "cauldron" / "cauldron.toml"


def container_name(project_dir=None, override=None):
    """Return the container name for a project directory.

    If override is provided, it is returned as-is. Otherwise the name is
    `cauldron-<directory-name>`.
    """
    if override:
        return override
    directory = pathlib.Path(project_dir or ".").resolve()
    return f"{DEFAULT_CONTAINER_PREFIX}-{directory.name}"


def project_image_name(project_dir=None):
    """Return the local image tag for a project."""
    directory = pathlib.Path(project_dir or ".").resolve()
    return f"{DEFAULT_CONTAINER_PREFIX}-{directory.name}:latest"


def project_dir():
    """Return the absolute path to the current project directory."""
    return pathlib.Path.cwd().resolve()


def find_dockerfile():
    """Return the path to the custom Dockerfile, or None if none exists.

    Prefers the project-level file over the global file.
    """
    project_file = pathlib.Path.cwd() / DOCKERFILE_PROJECT_PATH
    if project_file.exists():
        return project_file

    global_file = DOCKERFILE_GLOBAL_PATH
    if global_file.exists():
        return global_file

    return None


def host_user():
    """Return the host user's UID and GID as strings."""
    return str(os.getuid()), str(os.getgid())
