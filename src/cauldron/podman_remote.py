"""Prepare a Podman client and socket for the host Podman service.

When the ``podman_socket`` option is enabled, the container talks to the host's
rootless Podman service instead of running its own nested Podman. The client
shipped in the image may be newer than the host server and refuse the
connection, so a static ``podman-remote`` binary matching the host's server
version is downloaded from the upstream release on the host and mounted into
the container.
"""

import hashlib
import os
import pathlib
import platform
import re
import shutil
import socket
import subprocess
import tarfile
import urllib.error
import urllib.request

from cauldron import podman

RELEASE_URL = "https://github.com/containers/podman/releases/download"


class RemoteError(Exception):
    """Raised when the remote Podman client or socket cannot be prepared."""


def _arch():
    """Return the release architecture name for the current machine."""
    machine = platform.machine().lower()
    return {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine, machine)


def server_version():
    """Return the host Podman server version, or None if it cannot be read."""
    for key in ("Server", "Client"):
        result = subprocess.run(
            [
                podman._podman_bin(),
                "version",
                "--format",
                f"{{{{.{key}.Version}}}}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().lstrip("v")
    return None


def _cache_dir(version):
    """Return the cache directory for a Podman version."""
    base = os.environ.get("XDG_CACHE_HOME") or str(pathlib.Path.home() / ".cache")
    return pathlib.Path(base) / "cauldron" / "podman-remote" / version


def _asset_names(version, arch):
    """Return candidate release asset names, most likely first.

    Older releases only published an architecture-neutral ``podman-remote-
    static.tar.gz``; newer releases publish an architecture-suffixed archive.
    """
    suffixed = f"podman-remote-static-linux_{arch}.tar.gz"
    neutral = "podman-remote-static.tar.gz"
    try:
        major = int(version.split(".", 1)[0])
    except ValueError:
        major = 0
    if major >= 4:
        return [suffixed, neutral]
    return [neutral, suffixed]


def _checksum_url(version):
    return f"{RELEASE_URL}/v{version}/shasums"


def _asset_url(version, asset):
    return f"{RELEASE_URL}/v{version}/{asset}"


def _fetch_checksums(version):
    """Return a mapping of asset name to sha256, or an empty dict."""
    try:
        with urllib.request.urlopen(_checksum_url(version), timeout=60) as response:
            text = response.read().decode()
    except (urllib.error.URLError, OSError):
        return {}
    checksums = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            checksums[parts[1]] = parts[0]
    return checksums


def _download(url, destination):
    """Download ``url`` to ``destination``, raising HTTPError on failure."""
    with (
        urllib.request.urlopen(url, timeout=120) as response,
        open(destination, "wb") as target,
    ):
        shutil.copyfileobj(response, target)
    return destination


def _verify(path, expected):
    """Verify the sha256 of ``path`` against ``expected``."""
    if not expected:
        return
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        path.unlink()
        raise RemoteError(f"Checksum mismatch for {path.name}")


def _find_binary(directory):
    """Locate the Podman binary inside an extracted archive."""
    extracted = list(pathlib.Path(directory).rglob("*"))
    for path in extracted:
        if path.is_file() and path.name in ("podman", "podman-remote"):
            return path
    files = [path for path in extracted if path.is_file()]
    if not files:
        raise RemoteError("No Podman binary found in the downloaded archive")
    return max(files, key=lambda path: path.stat().st_size)


def ensure_client(version=None, arch=None):
    """Return the path to a static client matching the host server version.

    The binary is downloaded from the upstream release on first use and cached
    under ``~/.cache/cauldron/podman-remote/<version>``.
    """
    version = version or server_version()
    if not version:
        raise RemoteError(
            "Could not determine the host Podman version. "
            "Is Podman installed and working?"
        )
    arch = arch or _arch()

    directory = _cache_dir(version)
    binary = directory / "podman"
    if binary.exists():
        return binary

    directory.mkdir(parents=True, exist_ok=True)
    checksums = _fetch_checksums(version)

    last_error = None
    for asset in _asset_names(version, arch):
        archive = directory / asset
        try:
            _download(_asset_url(version, asset), archive)
        except urllib.error.HTTPError as exc:
            last_error = exc
            continue
        _verify(archive, checksums.get(asset))
        with tarfile.open(archive) as tar:
            tar.extractall(directory, filter="data")
        archive.unlink()
        found = _find_binary(directory)
        found.chmod(0o755)
        if found != binary:
            shutil.move(str(found), str(binary))
        return binary

    raise RemoteError(
        f"No static podman-remote binary found for Podman {version} ({arch})."
    ) from last_error


def socket_path():
    """Return the default rootless Podman socket path on the host."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return pathlib.Path(runtime) / "podman" / "podman.sock"


def ensure_socket():
    """Return the host Podman socket path, or raise if it is not available."""
    path = socket_path()
    if not path.exists():
        raise RemoteError(
            f"Podman socket not found at {path}. Enable it on the host with "
            "'systemctl --user enable --now podman.socket'."
        )
    return path


def running_in_remote():
    """Return True when running inside a cauldron that already talks to the host.

    This is the nested case: a cauldron created with ``podman_socket`` is itself
    running ``cauldron`` commands. ``run_container`` points the container at the
    mounted host socket through ``CONTAINER_HOST``, so that value marks a
    container whose Podman commands already reach the host.
    """
    return "/run/podman-host" in os.environ.get("CONTAINER_HOST", "")


def _container_id():
    """Best-effort id of the current container, for inspecting its own mounts.

    ``CAULDRON_CONTAINER_ID`` overrides this. Otherwise the container's hostname
    is used, since Podman sets it to the (short) container id; the cgroup is a
    final fallback when its namespace exposes the host hierarchy.
    """
    override = os.environ.get("CAULDRON_CONTAINER_ID", "").strip()
    if override:
        return override
    hostname = (os.environ.get("HOSTNAME") or socket.gethostname() or "").strip()
    if hostname:
        return hostname
    try:
        text = pathlib.Path("/proc/self/cgroup").read_text()
    except OSError:
        return None
    match = re.search(r"(?:libpod|podman|docker)[-_.]?([0-9a-f]{6,64})", text)
    return match.group(1) if match else None


def host_backend_paths():
    """Return the host-side socket and client paths for a sibling container.

    When nested, the host socket directory and static client are already
    bind-mounted into this container from the host. Reusing those exact host
    paths (read from the current container's mounts) lets a newly created
    sibling share the same socket and client, without re-resolving or
    re-downloading anything on the host.
    """
    cid = _container_id()
    if not cid:
        raise RemoteError(
            "Could not determine the current container id. Set "
            "CAULDRON_CONTAINER_ID to the container id when running cauldron "
            "inside a cauldron."
        )

    socket_dir = None
    client = None
    for mount in podman.container_mounts(cid):
        destination = mount.get("Destination")
        if destination == "/run/podman-host":
            socket_dir = mount.get("Source")
        elif destination == "/usr/local/bin/cauldron-podman":
            client = mount.get("Source")
    if not socket_dir or not client:
        raise RemoteError(
            f"Could not find the host Podman socket and client mounts on "
            f"container {cid}. Was this cauldron created with podman_socket "
            "enabled?"
        )

    socket_name = "podman.sock"
    container_host = os.environ.get("CONTAINER_HOST", "")
    if container_host.startswith("unix://"):
        tail = container_host[len("unix://") :].rsplit("/", 1)[-1]
        if tail:
            socket_name = tail
    return pathlib.Path(socket_dir) / socket_name, pathlib.Path(client)


def host_sibling_mounts(project_dir):
    """Return the current cauldron's host->container bind mounts for a sibling.

    Re-deriving the config's mounts by expanding env vars inside this container
    yields cauldron-local paths the host cannot bind-mount, so instead reuse the
    actual host paths this container was mounted from. The cauldron-managed
    mounts (project dir, host socket, static client) are omitted; run_container
    adds those itself.
    """
    skip = {str(project_dir), "/run/podman-host", "/usr/local/bin/cauldron-podman"}
    mounts = []
    for mount in podman.container_mounts(_container_id()):
        destination = mount.get("Destination")
        source = mount.get("Source")
        if not source or destination in skip:
            continue
        mounts.append(
            {
                "source": source,
                "target": destination,
                "options": "ro" if not mount.get("RW", True) else "rw",
            }
        )
    return mounts


def host_sibling_env():
    """Return the current cauldron's environment for a sibling container.

    Reuses the host-resource values already set on this container (for example
    ``SSH_AUTH_SOCK``) instead of re-expanding env vars in-container. The keys
    run_container manages itself (HOME, CONTAINER_HOST, CAULDRON_PODMAN_PATH)
    are dropped so it can set them for the sibling.
    """
    managed = {"HOME", "CONTAINER_HOST", "CAULDRON_PODMAN_PATH"}
    return {
        key: value
        for key, value in podman.container_env_of(_container_id()).items()
        if key not in managed
    }
