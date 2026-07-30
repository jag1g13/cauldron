import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import warnings

from cauldron import config as config_module
from cauldron.project import DEFAULT_CONTAINER_PREFIX

BASE_IMAGE = "docker.io/library/debian:trixie-slim"
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


def image_id(image):
    """Return the image ID, or None if the image is not found."""
    result = _run(["image", "inspect", image, "-f", "{{.Id}}"])
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def image_env(image):
    """Return environment variables defined in an image's config.

    Returns a dict mapping variable names to values.
    """
    result = _run(["image", "inspect", image, "--format", "{{json .Config.Env}}"])
    if result.returncode != 0:
        return {}

    try:
        env_list = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return {}

    env = {}
    for item in env_list:
        if isinstance(item, str) and "=" in item:
            key, value = item.split("=", 1)
            env[key] = value
    return env


def ensure_base_image(base_image=None):
    """Ensure the base image exists locally, pulling it if necessary.

    If ``base_image`` is provided, it is used instead of the default base
    image. The image is no longer re-tagged as ``cauldron-base:latest``; the
    original image reference is used directly and passed to builds via the
    ``CAULDRON_BASE`` build argument.
    """
    base = base_image or BASE_IMAGE
    if image_exists(base):
        return True
    return pull(base)


def run_test_container(base_image=None):
    """Run a throwaway container from the base image to verify it works."""
    base = base_image or BASE_IMAGE
    return _run(["run", "--rm", base, "echo", "cauldron-check-ok"]).returncode == 0


def build_image(tag, dockerfile, context, build_args=None):
    """Build an image from a Dockerfile and tag it.

    ``build_args`` is an optional dictionary of build arguments passed to
    Podman as ``--build-arg key=value``.

    Returns True on success.
    """
    args = [
        "build",
        "-t",
        tag,
        "-f",
        str(dockerfile),
    ]
    for key, value in (build_args or {}).items():
        args.extend(["--build-arg", f"{key}={value}"])
    args.append(str(context))
    result = _run(args)
    return result.returncode == 0


def build_project_image(tag, uid, gid, base_image=None, scripts=None, project_dir=None):
    """Build the final project image with the host user configured.

    The base image is supplied through the ``CAULDRON_BASE`` build argument.
    The resulting image creates a user matching the host UID/GID and sets
    HOME to /home/cauldron.

    If ``scripts`` is provided, each hook is copied into the image as an
    executable script at ``/usr/local/share/cauldron/<hook>.sh``. Inline
    script content is written to a temporary file; file paths are read from
    the project directory. If an ``entrypoint`` hook is present, the image
    entrypoint is set to run it.

    Returns True on success.
    """
    base = base_image or BASE_IMAGE
    scripts = scripts or {}
    project_dir = pathlib.Path(project_dir or ".").resolve()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = pathlib.Path(tmpdir)
        script_files = {}

        for hook, value in scripts.items():
            if _is_inline_script(value):
                content = value
            else:
                path = project_dir / pathlib.Path(
                    config_module._expand_host_vars(value)
                )
                content = path.read_text()

            script_path = tmpdir / f"{hook}.sh"
            script_path.write_text(content)
            script_files[hook] = f"{hook}.sh"

        lines = [
            "ARG CAULDRON_BASE",
            "FROM ${CAULDRON_BASE}",
            "USER root",
            f"RUN groupadd -g {gid} -o cauldron && useradd -m -u {uid} -g {gid} -o cauldron && usermod -p '*' cauldron",
            "RUN apt-get update && apt-get install -y --no-install-recommends openssh-server && rm -rf /var/lib/apt/lists/*",
            f"RUN mkdir -p /run/sshd /home/cauldron/.ssh && chown {uid}:{gid} /home/cauldron/.ssh && chmod 700 /home/cauldron/.ssh",
            f"ENV HOME={CONTAINER_HOME}",
        ]

        for hook, filename in script_files.items():
            lines.append(f"COPY {filename} /usr/local/share/cauldron/{hook}.sh")
            lines.append(f"RUN chmod +x /usr/local/share/cauldron/{hook}.sh")

        if "entrypoint" in script_files:
            lines.append('ENTRYPOINT ["/usr/local/share/cauldron/entrypoint.sh"]')

        dockerfile = tmpdir / "Dockerfile"
        dockerfile.write_text("\n".join(lines) + "\n")
        return build_image(tag, dockerfile, tmpdir, build_args={"CAULDRON_BASE": base})


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


def _selinux_enabled():
    """Return True if SELinux is installed and currently enforcing/permissive."""
    try:
        result = subprocess.run(["selinuxenabled"], capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _has_selinux_option(options):
    """Return True if options contains an SELinux relabel option (z or Z)."""
    return any(opt in ("z", "Z") for opt in options.split(","))


def _is_inline_script(value):
    """Return True if a script value looks like inline script content.

    Multi-line strings and strings starting with a shebang are treated as
    inline content; everything else is treated as a file path.
    """
    if not isinstance(value, str):
        return False
    value = value.strip()
    return "\n" in value or value.startswith("#!")


def _format_mount_spec(mount):
    """Format a normalized mount dict as a Podman -v argument."""
    options = mount.get("options", "")
    if options:
        return f"{mount['source']}:{mount['target']}:{options}"
    return f"{mount['source']}:{mount['target']}"


def run_container(
    name,
    image,
    workdir,
    project_dir,
    uid,
    gid,
    env=None,
    mounts=None,
    ports=None,
    known_hosts=None,
    entrypoint=False,
):
    """Create and start a detached container with the standard mounts.

    The optional ``env`` dict sets extra environment variables. If a PATH
    value contains ``${PATH}``, it is expanded with the image's default PATH.

    ``mounts`` is a list of dicts with ``source``, ``target`` and ``options``.
    ``ports`` is a list of strings in Podman's ``-p`` syntax.
    ``known_hosts`` is a list of strings in ``hostname:ip`` format, passed to
    Podman's ``--add-host`` flag.

    When ``entrypoint`` is True, the image's configured entrypoint is used and
    no default ``sleep infinity`` command is appended. This is used when the
    user has configured a custom ``entrypoint`` lifecycle script.

    Git configuration and SSH agent forwarding are no longer handled here;
    add them as ordinary mounts and environment variables in the config file.

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
        f"{project_dir}:{project_dir}:rw,Z",
    ]

    args.extend(["-e", f"HOME={CONTAINER_HOME}"])

    selinux = _selinux_enabled()
    for mount in mounts or []:
        spec = _format_mount_spec(mount)
        args.extend(["-v", spec])
        if selinux and not _has_selinux_option(mount.get("options", "")):
            warnings.warn(
                f"Mount {mount['target']!r} has no SELinux relabel option "
                "(z/Z); add it to options if files are not accessible"
            )

    for port in ports or []:
        args.extend(["-p", port])

    for entry in known_hosts or []:
        args.extend(["--add-host", entry])

    env = env or {}
    if "PATH" in env and "${PATH}" in env["PATH"]:
        image_path = image_env(image).get("PATH", "")
        env["PATH"] = env["PATH"].replace("${PATH}", image_path)

    for key, value in env.items():
        args.extend(["-e", f"{key}={value}"])

    args.append(image)
    if not entrypoint:
        args.extend(["sleep", "infinity"])

    return _run(args).returncode == 0


def start_container(name):
    """Start a stopped container. Returns True on success."""
    return _run(["start", name]).returncode == 0


def restart_container(name):
    """Restart a container. Returns True on success."""
    return _run(["restart", name]).returncode == 0


def container_id(name):
    """Return the container ID for the named container, or None if not found."""
    result = _run(["container", "inspect", "-f", "{{.Id}}", name])
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def stop_container(name):
    """Stop a running container. Returns True on success."""
    return _run(["stop", name]).returncode == 0


def container_shell(name, fallback="bash"):
    """Return the container user's $SHELL, or fallback if unset or empty."""
    result = _run(["exec", name, "printenv", "SHELL"])
    if result.returncode != 0:
        return fallback
    shell = result.stdout.strip()
    return shell or fallback


def exec_in_container(name, command, args=None, interactive=False, tty=False):
    """Run a command inside a container, attaching stdio to the terminal.

    Returns the command's exit code.
    """
    cmd = ["podman", "exec"]
    if interactive:
        cmd.append("-i")
    if tty:
        cmd.append("-t")
    cmd.append(name)
    cmd.append(command)
    if args:
        cmd.extend(args)

    try:
        return subprocess.run(cmd).returncode
    except FileNotFoundError:
        return 1


def exec_hook_in_container(name, command):
    """Run a non-interactive lifecycle hook inside a container.

    When verbose mode is enabled the hook's output is streamed to the
    terminal. Otherwise output is captured silently and only the exit code is
    returned.

    Returns the command's exit code.
    """
    cmd = ["podman", "exec", name, command]
    try:
        if _verbose:
            return subprocess.run(cmd).returncode
        return subprocess.run(cmd, capture_output=True, text=True).returncode
    except FileNotFoundError:
        return 1


def exec_check(name, command, user=None):
    """Return True if a command succeeds in the container, False otherwise.

    ``command`` is a list of arguments. ``user`` optionally overrides the
    container user (e.g. ``"0"`` for root).
    """
    args = ["podman", "exec"]
    if user:
        args.extend(["-u", user])
    args.append(name)
    args.extend(command)
    try:
        return subprocess.run(args, capture_output=True, text=True).returncode == 0
    except FileNotFoundError:
        return False


def exec_capture(name, command, user=None):
    """Run a command in a container and return stdout, or None on failure."""
    args = ["podman", "exec"]
    if user:
        args.extend(["-u", user])
    args.append(name)
    args.extend(command)
    try:
        result = subprocess.run(args, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    except FileNotFoundError:
        return None


def exec_with_stdin(name, command, input_text, user=None):
    """Run a command in a container, feeding stdin. Returns True on success."""
    args = ["podman", "exec", "-i"]
    if user:
        args.extend(["-u", user])
    args.append(name)
    args.extend(command)
    try:
        result = subprocess.run(args, input=input_text, capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


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
