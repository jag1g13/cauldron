import json
import pathlib
import subprocess
import sys
import tempfile
import threading

from cauldron.project import DEFAULT_CONTAINER_PREFIX

BASE_IMAGE = "docker.io/library/debian:trixie-slim"
LOCAL_BASE_TAG = "cauldron-base:latest"
CONTAINER_HOME = "/home/cauldron"
PROJECT_LABEL = "cauldron.project_dir"

_verbose = False


def set_verbose(verbose):
    """Enable or disable verbose mode for podman subcommands."""
    global _verbose
    _verbose = verbose


def _stream_pipe(pipe, lines, file):
    """Read lines from a pipe, append to lines, and print to file."""
    for line in iter(pipe.readline, ""):
        lines.append(line)
        print(line, end="", file=file)
    pipe.close()


def _run(args, **kwargs):
    """Run a podman subcommand and return the CompletedProcess.

    Returns a CompletedProcess with a non-zero returncode if podman is not
    installed or the command fails. In verbose mode, stdout and stderr are
    streamed to the terminal in real time while still being captured.
    """
    command = ["podman", *args]
    try:
        if _verbose:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                **kwargs,
            )
            stdout_lines = []
            stderr_lines = []
            stdout_thread = threading.Thread(
                target=_stream_pipe,
                args=(process.stdout, stdout_lines, sys.stdout),
            )
            stderr_thread = threading.Thread(
                target=_stream_pipe,
                args=(process.stderr, stderr_lines, sys.stderr),
            )
            stdout_thread.start()
            stderr_thread.start()
            returncode = process.wait()
            stdout_thread.join()
            stderr_thread.join()
            return subprocess.CompletedProcess(
                args=command,
                returncode=returncode,
                stdout="".join(stdout_lines),
                stderr="".join(stderr_lines),
            )

        return subprocess.run(command, capture_output=True, text=True, **kwargs)
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            args=command, returncode=1, stdout="", stderr="podman not found"
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


def build_image(tag, dockerfile, context):
    """Build an image from a Dockerfile and tag it.

    Returns True on success.
    """
    result = _run(
        [
            "build",
            "-t",
            tag,
            "-f",
            str(dockerfile),
            str(context),
        ]
    )
    return result.returncode == 0


def build_project_image(tag, uid, gid, intermediate_tag=None):
    """Build the final project image with the host user configured.

    If intermediate_tag is provided, it is used as the base image; otherwise
    cauldron-base:latest is used. The resulting image creates a user matching
    the host UID/GID and sets HOME to /home/cauldron.

    Returns True on success.
    """
    base = intermediate_tag or LOCAL_BASE_TAG
    dockerfile_content = f"""FROM {base}
USER root
RUN groupadd -g {gid} -o cauldron && useradd -m -u {uid} -g {gid} -o cauldron
ENV HOME={CONTAINER_HOME}
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        dockerfile = pathlib.Path(tmpdir) / "Dockerfile"
        dockerfile.write_text(dockerfile_content)
        return build_image(tag, dockerfile, tmpdir)


def container_exists(name):
    """Return True if a container with the given name exists."""
    return _run(["container", "exists", name]).returncode == 0


def container_running(name):
    """Return True if the named container is running."""
    result = _run(
        [
            "container",
            "inspect",
            "-f",
            "{{.State.Running}}",
            name,
        ]
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip().lower() == "true"


def run_container(
    name,
    image,
    workdir,
    project_dir,
    uid,
    gid,
    gitconfig=None,
    ssh_auth_sock=None,
):
    """Create and start a detached container with the standard mounts.

    Returns True on success.
    """
    args = [
        "run",
        "-d",
        "--name",
        name,
        "--label",
        f"{PROJECT_LABEL}={project_dir}",
        "--user",
        f"{uid}:{gid}",
        "--userns",
        "keep-id",
        "-w",
        str(workdir),
        "-v",
        f"{project_dir}:{project_dir}:rw",
    ]

    args.extend(["-e", f"HOME={CONTAINER_HOME}"])

    if gitconfig:
        args.extend(["-v", f"{gitconfig}:{CONTAINER_HOME}/.gitconfig:ro,Z"])

    if ssh_auth_sock:
        args.extend(["-v", f"{ssh_auth_sock}:{ssh_auth_sock}:ro"])
        args.extend(["-e", f"SSH_AUTH_SOCK={ssh_auth_sock}"])

    args.append(image)

    return _run(args).returncode == 0


def stop_container(name):
    """Stop a running container. Returns True on success."""
    return _run(["stop", name]).returncode == 0


def remove_container(name, force=False):
    """Remove a container. Returns True on success."""
    args = ["rm"]
    if force:
        args.append("-f")
    args.append(name)
    return _run(args).returncode == 0


def list_containers(all_containers=False):
    """List Cauldron-managed containers.

    Returns a list of dictionaries with keys: name, image, status, project_dir.
    """
    args = [
        "ps",
        "--format",
        "json",
        "--filter",
        f"name=^{DEFAULT_CONTAINER_PREFIX}-",
    ]
    if all_containers:
        args.append("--all")

    result = _run(args)
    if result.returncode != 0:
        return []

    try:
        containers = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []

    if not isinstance(containers, list):
        containers = [containers]

    return [
        {
            "name": c.get("Names", [c.get("Id", "")])[0]
            if isinstance(c.get("Names"), list)
            else c.get("Names", ""),
            "image": c.get("Image", ""),
            "status": c.get("State", c.get("Status", "")),
            "project_dir": c.get("Labels", {}).get(PROJECT_LABEL, ""),
        }
        for c in containers
    ]
