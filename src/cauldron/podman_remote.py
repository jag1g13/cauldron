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
import shutil
import subprocess
import tarfile
import urllib.error
import urllib.request

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
            ["podman", "version", "--format", f"{{{{.{key}.Version}}}}"],
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
