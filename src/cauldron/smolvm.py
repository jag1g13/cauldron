"""smolvm backend: run each project in a persistent smol machine.

Mirrors the surface of :mod:`cauldron.podman` so the CLI can treat a smol
machine like a container. Machine operations drive the ``smolvm`` CLI through
subprocess (selectable for tests via ``CAULDRON_SMOLVM_PATH``); the project
image is built with the host builder (``podman`` or ``docker``) and handed to
smolvm as a ``docker save`` tar.

Notable CLI facts (validated by the spike):
- ``machine create``/``start``/``stop``/``delete`` manage a named machine.
- ``machine exec`` takes the machine as ``--name <n>`` (not a positional), and
  the command after ``--``.
- ``machine ls --json`` lists machines with name, state, image, labels and a
  port *count* (smolvm does not expose the host:guest mapping).
"""

import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import warnings

from cauldron import imagebuild
from cauldron.project import DEFAULT_CONTAINER_PREFIX

BASE_IMAGE = "docker.io/library/debian:trixie-slim"
CONTAINER_HOME = "/home/cauldron"
PROJECT_LABEL = "cauldron.project_dir"
KVM_DEVICE = "/dev/kvm"

_verbose = False


def set_verbose(verbose):
    """Enable or disable verbose mode for smolvm subcommands."""
    global _verbose
    _verbose = verbose


def _smolvm_bin():
    """Return the smolvm executable to invoke (overridable for tests)."""
    return os.environ.get("CAULDRON_SMOLVM_PATH", "smolvm")


def _stream_pipe(pipe, lines, file):
    for line in iter(pipe.readline, ""):
        lines.append(line)
        print(line, end="", file=file)
    pipe.close()


def _run(command, quiet=False, **kwargs):
    """Run a full command list; stream output in verbose mode.

    Returns a CompletedProcess, or one with returncode 1 if the binary is
    missing.
    """
    try:
        if _verbose and not quiet:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                **kwargs,
            )
            stdout_lines, stderr_lines = [], []
            stdout_thread = threading.Thread(
                target=_stream_pipe, args=(process.stdout, stdout_lines, sys.stdout)
            )
            stderr_thread = threading.Thread(
                target=_stream_pipe, args=(process.stderr, stderr_lines, sys.stderr)
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
            args=command, returncode=1, stdout="", stderr="command not found"
        )


def _smolvm(args, quiet=False, **kwargs):
    """Run a ``smolvm`` subcommand and return the CompletedProcess."""
    return _run([_smolvm_bin(), *args], quiet=quiet, **kwargs)


# --------------------------------------------------------------------------
# Host builder (builds the project image; smolvm itself cannot build images)
# --------------------------------------------------------------------------


def _host_builder():
    """Return the host image builder to build with: ``podman`` or ``docker``."""
    if shutil.which("podman"):
        return "podman"
    if shutil.which("docker"):
        return "docker"
    return None


def _builder_build(tag, dockerfile, context, build_args=None, build_network=None):
    """Build an image with the host builder and tag it.

    ``build_network`` is an optional value for the builder's ``--network``
    flag (for example ``host``), accepted by both podman and docker.
    """
    builder = _host_builder()
    if builder is None:
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="no host builder (podman or docker) found to build the project image",
        )
    args = [builder, "build", "-t", tag, "-f", str(dockerfile)]
    if build_network:
        args.extend(["--network", build_network])
    for key, value in (build_args or {}).items():
        args.extend(["--build-arg", f"{key}={value}"])
    args.append(str(context))
    return _run(args)


def _builder_save(tag, dest):
    """Save a built image to a docker-archive tar at ``dest``.

    The archive is written to a temporary sibling and renamed into place, so
    the export never targets an existing tar: podman/buildah refuse to append
    to a docker-archive and fail with "doesn't support modifying existing
    images".
    """
    builder = _host_builder()
    if builder is None:
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="no host builder (podman or docker) found to export the project image",
        )
    dest = pathlib.Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Export to a unique, initially nonexistent path on the destination
    # filesystem (avoids the fixed name colliding with a stale or concurrent
    # export), then rename it into place atomically; clean the temp up on
    # failure.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{dest.name}.", suffix=".tmp", dir=str(dest.parent)
    )
    os.close(fd)
    tmp = pathlib.Path(tmp_name)
    try:
        result = _run([builder, "save", "-o", str(tmp), tag])
        if result.returncode == 0:
            os.replace(tmp, dest)
        return result
    finally:
        if tmp.exists():
            tmp.unlink()


def image_tar_path(tag):
    """Return the on-disk path for a built image's docker-save tar.

    Overridable via ``CAULDRON_SMOLTAR_DIR`` (used by tests).
    """
    directory = pathlib.Path(
        os.environ.get("CAULDRON_SMOLTAR_DIR", "~/.cache/cauldron/smoltar")
    ).expanduser()
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", tag)
    return directory / f"{safe}.tar"


def _image_default_path(tag):
    """Return the default PATH from an image's config, via the host builder."""
    builder = _host_builder()
    if builder == "podman":
        result = _run(
            ["podman", "image", "inspect", tag, "--format", "{{json .Config.Env}}"],
            quiet=True,
        )
    elif builder == "docker":
        result = _run(
            ["docker", "inspect", tag, "--format", "{{json .Config.Env}}"], quiet=True
        )
    else:
        return ""
    if result.returncode != 0:
        return ""
    try:
        env_list = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return ""
    for item in env_list:
        if isinstance(item, str) and item.startswith("PATH="):
            return item.split("=", 1)[1]
    return ""


# --------------------------------------------------------------------------
# Image operations
# --------------------------------------------------------------------------


def version():
    """Return True if ``smolvm --version`` succeeds."""
    return _smolvm(["--version"]).returncode == 0


def _path_writable(path):
    """Return True if this user can write ``path`` (ACL-aware check)."""
    return os.access(path, os.W_OK)


def kvm_check():
    """Verify that smolvm can use the KVM device on this machine.

    Returns ``(ok, hint)``. ``ok`` is False when the KVM device is missing or
    not writable by this user — microVMs cannot start in that case — and
    ``hint`` explains how to grant access.
    """
    if not os.path.exists(KVM_DEVICE):
        return (
            False,
            f"{KVM_DEVICE} is missing. The smolvm backend needs KVM: "
            "enable hardware virtualization in BIOS/UEFI and load the kvm module.",
        )
    if not _path_writable(KVM_DEVICE):
        return (
            False,
            f"{KVM_DEVICE} is not writable by this user, so smolvm cannot "
            "start microVMs. On the host, add this user to the kvm group, or "
            f"grant a named ACL: `sudo setfacl -m u:{os.getuid()}:rw "
            f"{KVM_DEVICE}`. Inside a container, group membership does not "
            "cross user namespaces — use the setfacl form instead.",
        )
    return True, ""


def image_exists(tag):
    """Return True if the built image's tar is present on disk."""
    return image_tar_path(tag).exists()


def _base_image_exists(base):
    """Return True if the base image is present in the host builder's store.

    Both ``podman`` and ``docker`` accept ``image inspect``, which succeeds
    only when the image is already present.
    """
    builder = _host_builder()
    if builder is None:
        return False
    return _run([builder, "image", "inspect", base], quiet=True).returncode == 0


def ensure_base_image(base_image=None):
    """Ensure the base image is available to the host builder (pull if needed).

    Mirrors the Podman backend: the image is pulled only when it is not already
    present in the host builder's local store, so a locally built base tag
    works and cached registry images are usable offline.
    """
    base = base_image or BASE_IMAGE
    builder = _host_builder()
    if builder is None:
        return False
    if _base_image_exists(base):
        return True
    return _run([builder, "pull", base]).returncode == 0


def run_test_container(base_image=None):
    """Run a throwaway machine from the base image to verify it boots."""
    base = base_image or BASE_IMAGE
    return (
        _smolvm(
            ["machine", "run", "--net", "--image", base, "--", "echo", "ok"]
        ).returncode
        == 0
    )


def build_image(tag, dockerfile, context, build_args=None, build_network=None):
    """Build an image from a Dockerfile with the host builder and tag it.

    ``build_network`` is an optional value for the builder's ``--network``
    flag (for example ``host``).
    """
    return _builder_build(
        tag, dockerfile, context, build_args=build_args, build_network=build_network
    )


def build_project_image(
    tag, uid, gid, base_image=None, scripts=None, project_dir=None, build_network=None
):
    """Build the final project image and export it as a docker-save tar.

    The image is built with the host builder (base image supplied through the
    ``CAULDRON_BASE`` build argument) and then saved to :func:`image_tar_path`.
    ``build_network`` is an optional value for the builder's ``--network``
    flag (for example ``host``).
    Returns the ``CompletedProcess`` of the (last) step; check ``returncode``.
    """
    base = base_image or BASE_IMAGE
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = pathlib.Path(tmpdir)
        build_args = imagebuild.materialize_project_build(
            tmpdir, uid, gid, base, scripts, project_dir
        )
        result = _builder_build(
            tag,
            tmpdir / "Dockerfile",
            tmpdir,
            build_args=build_args,
            build_network=build_network,
        )
    if result.returncode == 0:
        result = _builder_save(tag, image_tar_path(tag))
    return result


# --------------------------------------------------------------------------
# Machine (container) state
# --------------------------------------------------------------------------


def _machines():
    """Return the parsed ``machine ls --json`` list (empty on failure)."""
    result = _smolvm(["machine", "ls", "--json"], quiet=True)
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return data if isinstance(data, list) else []


def _field(entry, *names):
    """Return the first non-None value of ``names`` present in ``entry``."""
    for name in names:
        if name in entry and entry[name] is not None:
            return entry[name]
    return None


def container_exists(name):
    """Return True if a machine with the given name exists."""
    return any(_field(m, "name", "Name") == name for m in _machines())


def container_running(name):
    """Return True if the named machine is running."""
    for m in _machines():
        if _field(m, "name", "Name") == name:
            state = str(_field(m, "state", "State", "status", "Status", "")).lower()
            return state == "running"
    return False


def container_id(name):
    """Return the machine name if it exists, else None."""
    return name if container_exists(name) else None


def container_mounts(name):
    """Return the mount list for a machine.

    The smolvm backend does not use this (the /run/user repair is Podman-only);
    returns an empty list.
    """
    return []


# --------------------------------------------------------------------------
# Machine lifecycle
# --------------------------------------------------------------------------


def _format_volume(mount):
    """Format a normalized mount dict as a smolvm ``-v`` argument."""
    options = (mount.get("options") or "").split(",")
    suffix = ":ro" if "ro" in options else ""
    return f"{mount['source']}:{mount['target']}{suffix}"


def _format_socket_mount(mount):
    """Format a normalized mount dict as a smolvm ``--mount-socket`` argument.

    smolvm has no read-only form for socket mounts, so ``options`` are
    ignored.
    """
    return f"{mount['source']}:{mount['target']}"


def _format_port(port):
    """Translate a Podman ``-p`` spec to a smolvm ``host:guest[/proto]`` spec.

    Podman's ``host_ip:host:container`` host-IP binding is dropped (smolvm
    publishes on the default interfaces).
    """
    parts = port.split(":")
    proto = ""
    if "/" in parts[-1]:
        parts[-1], proto = parts[-1].split("/", 1)
    if len(parts) == 3:
        host, container = parts[1], parts[2]
    elif len(parts) == 2:
        host, container = parts[0], parts[1]
    else:
        host = container = parts[0]
    return f"{host}:{container}" + (f"/{proto}" if proto else "")


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
    devices=None,
    podman_socket=None,
    podman_client=None,
    entrypoint=False,
    nested=False,
    network=None,
):
    """Create and start a named machine from the built image tar.

    ``image`` is the project image tag; it is resolved to its docker-save tar.
    ``devices``, ``network``, ``podman_socket`` and ``podman_client`` are
    Podman-specific and ignored. ``nested`` passes smolvm's ``--nested`` so
    the guest can run its own VMs on its own /dev/kvm. Configured mounts are
    classified by their source path: Unix sockets go through
    ``--mount-socket``, directories through virtiofs ``-v`` (honouring
    ``ro``), and anything else (plain files, missing paths) is skipped with
    a warning, as virtiofs cannot share them.

    Returns the ``CompletedProcess`` of the start; check ``returncode``.
    """
    tar = image_tar_path(image)

    args = ["machine", "create", "--name", name]
    args.extend(["-I", str(tar)])
    args.extend(["--label", f"{PROJECT_LABEL}={project_dir}"])
    args.append("--net")
    if nested:
        args.append("--nested")
    args.extend(["-u", f"{uid}:{gid}"])
    if workdir:
        args.extend(["-w", str(workdir)])

    args.extend(["-v", f"{project_dir}:{project_dir}"])
    for mount in mounts or []:
        source = pathlib.Path(mount["source"])
        if source.is_socket():
            args.extend(["--mount-socket", _format_socket_mount(mount)])
        elif source.is_dir():
            args.extend(["-v", _format_volume(mount)])
        else:
            warnings.warn(
                f"Skipping mount {mount['source']!r}: smolvm can only mount "
                "directories (virtiofs) and sockets"
            )
    for port in ports or []:
        args.extend(["-p", _format_port(port)])

    env = dict(env or {})
    if "PATH" in env and "${PATH}" in env["PATH"]:
        env["PATH"] = env["PATH"].replace("${PATH}", _image_default_path(image))
    for key, value in env.items():
        args.extend(["-e", f"{key}={value}"])

    # known_hosts: smolvm has no --add-host, so write each hostname->ip mapping
    # into the guest /etc/hosts before the workload starts, mirroring Podman's
    # create-time --add-host. Config entries are "hostname:ip"; hosts(5) lines
    # are "ip hostname". A failing --init fails the machine start, so a broken
    # mapping fails `cauldron up` instead of reporting success.
    for entry in known_hosts or []:
        hostname, _, ip = entry.partition(":")
        if hostname and ip:
            args.extend(
                ["--init", f"echo {shlex.quote(ip)} {shlex.quote(hostname)} >> /etc/hosts"]
            )

    # The machine's long-lived workload keeps it running. When the image has a
    # custom entrypoint (from an ``entrypoint`` lifecycle script) it is used;
    # otherwise sleep so the machine stays up for exec/shell.
    if not entrypoint:
        args.extend(["--", "sleep", "infinity"])

    create = _smolvm(args)
    if create.returncode != 0:
        return create

    result = _smolvm(["machine", "start", "--name", name])
    if result.returncode != 0:
        return result
    return result


def start_container(name):
    """Start a stopped machine. Returns the ``CompletedProcess``."""
    return _smolvm(["machine", "start", "--name", name])


def restart_container(name):
    """Restart a machine. Returns the ``CompletedProcess``."""
    stop = _smolvm(["machine", "stop", "--name", name])
    return (
        _smolvm(["machine", "start", "--name", name]) if stop.returncode == 0 else stop
    )


def stop_container(name):
    """Stop a running machine. Returns True on success."""
    return _smolvm(["machine", "stop", "--name", name]).returncode == 0


def remove_container(name, force=False):
    """Remove a machine. Returns True on success.

    ``--force`` is always passed: smolvm asks for confirmation when stdin is
    not a terminal and refuses the delete without it — even for stopped
    machines. Cauldron is always non-interactive and the CLI already guards
    removing running machines unless the user passes ``--force``.
    """
    return _smolvm(["machine", "delete", "--name", name, "--force"]).returncode == 0


def container_shell(name, fallback="bash"):
    """Return the machine user's $SHELL, or fallback if unset or empty."""
    result = _smolvm(["machine", "exec", "--name", name, "--", "printenv", "SHELL"])
    if result.returncode != 0:
        return fallback
    shell = result.stdout.strip()
    return shell or fallback


# --------------------------------------------------------------------------
# Exec operations
# --------------------------------------------------------------------------


def exec_in_container(name, command, args=None, interactive=False, tty=False):
    """Run a command in a machine, attaching stdio to the terminal."""
    cmd = [_smolvm_bin(), "machine", "exec", "--name", name]
    if interactive:
        cmd.append("-i")
    if tty:
        cmd.append("-t")
    cmd.append("--")
    cmd.append(command)
    if args:
        cmd.extend(args)
    try:
        return subprocess.run(cmd).returncode
    except FileNotFoundError:
        return 1


def exec_hook_in_container(name, command):
    """Run a non-interactive lifecycle hook in a machine."""
    cmd = [_smolvm_bin(), "machine", "exec", "--name", name, "--", command]
    try:
        if _verbose:
            return subprocess.run(cmd).returncode
        return subprocess.run(cmd, capture_output=True, text=True).returncode
    except FileNotFoundError:
        return 1


def exec_check(name, command, user=None):
    """Return True if a command succeeds in the machine, False otherwise."""
    args = [_smolvm_bin(), "machine", "exec", "--name", name]
    if user:
        args.extend(["--user", user])
    args.extend(["--", *command])
    try:
        return subprocess.run(args, capture_output=True, text=True).returncode == 0
    except FileNotFoundError:
        return False


def exec_capture(name, command, user=None):
    """Run a command in a machine and return stdout, or None on failure."""
    args = [_smolvm_bin(), "machine", "exec", "--name", name]
    if user:
        args.extend(["--user", user])
    args.extend(["--", *command])
    try:
        result = subprocess.run(args, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    except FileNotFoundError:
        return None


def exec_with_stdin(name, command, input_text, user=None):
    """Run a command in a machine, feeding stdin. Returns True on success."""
    args = [_smolvm_bin(), "machine", "exec", "--name", name, "-i"]
    if user:
        args.extend(["--user", user])
    args.extend(["--", *command])
    try:
        result = subprocess.run(args, input=input_text, capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


# --------------------------------------------------------------------------
# Listing + port-conflict detection
# --------------------------------------------------------------------------


def list_containers(all_containers=False):
    """List Cauldron-managed machines.

    By default only running machines are listed; ``all_containers`` also includes
    stopped ones. Returns a list of dicts with keys: name, image, status, ports,
    host_ports, project_dir. Only machines named ``cauldron-<dir>`` are included.

    smolvm reports a port *count* rather than the host:guest mapping, so ``ports``
    is ``[str(count)]`` when ports are published and ``host_ports`` is empty.
    """
    result = []
    for m in _machines():
        name = _field(m, "name", "Name") or ""
        if not name.startswith(f"{DEFAULT_CONTAINER_PREFIX}-"):
            continue
        status = str(_field(m, "state", "State", "status", "Status") or "")
        if not all_containers and status.lower() != "running":
            continue
        labels = _field(m, "labels", "Labels") or {}
        port_count = _field(m, "ports", "Ports") or 0
        ports = [str(port_count)] if port_count else []
        result.append(
            {
                "name": name,
                "image": _field(m, "image", "Image") or "",
                "status": status,
                "ports": ports,
                "host_ports": [],
                "project_dir": labels.get(PROJECT_LABEL, "")
                if isinstance(labels, dict)
                else "",
            }
        )
    return result


def is_port_in_use_error(stderr):
    """Return True if ``stderr`` indicates a host port bind failure."""
    return bool(stderr) and any(
        token in stderr.lower()
        for token in (
            "address already in use",
            "in use",
            "already in use",
            "eaddrinuse",
        )
    )


def port_from_error(stderr):
    """Return the host port (int) from a port bind failure, or None.

    smolvm phrases the failure as ``cannot publish host TCP 127.0.0.1:<host>
    to guest TCP <guest>: ... in use``, with the guest port after the host
    port, so that shape is matched first. Other messages fall back to the
    last standalone number (not part of an IP address) before an "in use"
    phrase.
    """
    if not stderr:
        return None
    publish = re.search(r"publish host TCP (?:[\d.]+:)?(\d+)", stderr, re.IGNORECASE)
    if publish:
        return int(publish.group(1))
    phrase = re.search(r"(?:already in use|in use)", stderr, re.IGNORECASE)
    if not phrase:
        return None
    prefix = stderr[: phrase.start()]
    candidates = re.findall(r"(?<![.\d])(\d{1,5})(?![.\d])", prefix)
    if not candidates:
        return None
    return int(candidates[-1])


def find_port_owner(host_port):
    """Return the names of cauldron machines publishing ``host_port``.

    smolvm does not expose the host:guest port mapping, so a port cannot be
    attributed to a specific machine; this always returns an empty list. The
    port-conflict hint still works from the start error's own message.
    """
    return []


# --------------------------------------------------------------------------
# VSCode Remote-SSH proxy
# --------------------------------------------------------------------------


def ssh_proxy_command(container):
    """Return the sshd ProxyCommand that reaches a machine's sshd via exec.

    Mirrors the validated spike form (machine exec takes ``--name``).
    """
    return (
        f"{shlex.quote(_smolvm_bin())} machine exec --name {container} --user 0 -i --"
        " /usr/sbin/sshd -i -o UsePAM=no -o PasswordAuthentication=no"
    )
