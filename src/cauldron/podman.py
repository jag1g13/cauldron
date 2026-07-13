import subprocess

BASE_IMAGE = "docker.io/library/debian:trixie-slim"
LOCAL_BASE_TAG = "cauldron-base:latest"


def _run(args, **kwargs):
    """Run a podman subcommand and return the CompletedProcess.

    Returns a CompletedProcess with a non-zero returncode if podman is not
    installed or the command fails.
    """
    try:
        return subprocess.run(
            ["podman", *args], capture_output=True, text=True, **kwargs
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="podman not found"
        )


def version():
    """Return True if `podman version` succeeds."""
    return _run(["version"]).returncode == 0


def pull(image):
    """Pull an image from a registry."""
    return _run(["pull", image]).returncode == 0


def tag(source, target):
    """Tag a local image."""
    return _run(["tag", source, target]).returncode == 0


def image_exists(image):
    """Return True if the image exists locally."""
    return _run(["image", "exists", image]).returncode == 0


def ensure_base_image():
    """Ensure cauldron-base:latest exists, pulling and tagging if necessary."""
    if image_exists(LOCAL_BASE_TAG):
        return True
    if not pull(BASE_IMAGE):
        return False
    return tag(BASE_IMAGE, LOCAL_BASE_TAG)


def run_test_container():
    """Run a throwaway container from the base image to verify it works."""
    return (
        _run(["run", "--rm", LOCAL_BASE_TAG, "echo", "cauldron-check-ok"]).returncode
        == 0
    )
