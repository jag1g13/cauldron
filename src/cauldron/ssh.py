"""SSH setup for VSCode Remote-SSH integration."""

import pathlib
import re
import shutil
import subprocess

from cauldron import podman

SSH_DIR = pathlib.Path.home() / ".config" / "cauldron" / "ssh"
PRIVATE_KEY = SSH_DIR / "id_ed25519"
PUBLIC_KEY = SSH_DIR / "id_ed25519.pub"
SSH_CONFIG = SSH_DIR / "config"

USER_SSH_DIR = pathlib.Path.home() / ".ssh"
USER_SSH_CONFIG = USER_SSH_DIR / "config"

INCLUDE_MARKER_BEGIN = "# BEGIN cauldron"
INCLUDE_MARKER_END = "# END cauldron"

_CONTAINER_USER = "cauldron"
_CONTAINER_HOME = podman.CONTAINER_HOME


class SSHError(Exception):
    """Raised when SSH setup fails."""


def _keygen_available():
    """Return True if ssh-keygen is on PATH."""
    return shutil.which("ssh-keygen") is not None


def ensure_keypair():
    """Generate an ed25519 key pair if one does not already exist.

    The key pair is stored at ~/.config/cauldron/ssh/id_ed25519 with no
    passphrase. Raises :class:`SSHError` if ssh-keygen is unavailable or
    key generation fails.
    """
    if PRIVATE_KEY.exists() and PUBLIC_KEY.exists():
        return

    if not _keygen_available():
        raise SSHError(
            "ssh-keygen not found. Make sure OpenSSH is installed on the host."
        )

    SSH_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-f",
            str(PRIVATE_KEY),
            "-N",
            "",
            "-C",
            "cauldron",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SSHError(
            f"Failed to generate SSH key pair: {result.stderr.strip()}"
        )


def read_public_key():
    """Return the contents of the public key file."""
    return PUBLIC_KEY.read_text().strip()


def ensure_container_ssh(container):
    """Set up SSH inside the container for Remote-SSH access.

    Installs the public key as authorized_keys and generates host keys if
    missing. Raises :class:`SSHError` if the container lacks openssh-server
    (needs rebuild) or if any setup step fails.
    """
    if not podman.exec_check(container, ["test", "-f", "/usr/sbin/sshd"]):
        raise SSHError(
            "OpenSSH server not found in container. "
            "Rebuild the image with 'cauldron up --build'."
        )

    pubkey = read_public_key()

    if not podman.exec_with_stdin(
        container,
        [
            "sh",
            "-c",
            f"mkdir -p {_CONTAINER_HOME}/.ssh"
            f" && cat > {_CONTAINER_HOME}/.ssh/authorized_keys"
            f" && chmod 600 {_CONTAINER_HOME}/.ssh/authorized_keys",
        ],
        pubkey,
    ):
        raise SSHError("Failed to install authorized_keys in container.")

    if not podman.exec_check(container, ["ssh-keygen", "-A"], user="0"):
        raise SSHError("Failed to generate SSH host keys in container.")


def _build_host_block(container):
    """Build the SSH config block for a container."""
    return (
        f"Host {container}\n"
        f"    User {_CONTAINER_USER}\n"
        "    IdentityFile ~/.config/cauldron/ssh/id_ed25519\n"
        "    StrictHostKeyChecking no\n"
        "    UserKnownHostsFile /dev/null\n"
        f"    ProxyCommand podman exec -u 0 -i {container}"
        " /usr/sbin/sshd -i"
        " -o UsePAM=no -o PasswordAuthentication=no\n"
    )


def ensure_ssh_config(container):
    """Write the SSH config for a container.

    Manages a per-container block in ~/.config/cauldron/ssh/config and an
    Include directive in ~/.ssh/config so VSCode Remote-SSH finds it.
    """
    SSH_DIR.mkdir(parents=True, exist_ok=True)

    block = _build_host_block(container)
    begin = f"# BEGIN cauldron:{container}"
    end = f"# END cauldron:{container}"

    _update_managed_file(SSH_CONFIG, begin, end, block)
    SSH_CONFIG.chmod(0o600)

    _ensure_include_in_user_config()


def _update_managed_file(path, begin_marker, end_marker, content):
    """Insert or replace a marked block in a file."""
    existing = ""
    if path.exists():
        existing = path.read_text()

    pattern = re.compile(
        re.escape(begin_marker) + r".*?" + re.escape(end_marker),
        re.DOTALL,
    )

    new_block = f"{begin_marker}\n{content}{end_marker}\n"

    if pattern.search(existing):
        updated = pattern.sub(new_block, existing)
    else:
        separator = "\n" if existing and not existing.endswith("\n") else ""
        updated = existing + separator + new_block

    path.write_text(updated)


def _ensure_include_in_user_config():
    """Add an Include directive to ~/.ssh/config if not present."""
    USER_SSH_DIR.mkdir(parents=True, exist_ok=True)

    existing = ""
    if USER_SSH_CONFIG.exists():
        existing = USER_SSH_CONFIG.read_text()

    if INCLUDE_MARKER_BEGIN in existing:
        return

    new_block = (
        f"{INCLUDE_MARKER_BEGIN}\n"
        f"Include {SSH_CONFIG}\n"
        f"{INCLUDE_MARKER_END}\n"
    )
    separator = "\n" if existing and not existing.endswith("\n") else ""
    updated = existing + separator + new_block

    USER_SSH_CONFIG.write_text(updated)
    USER_SSH_CONFIG.chmod(0o600)
    try:
        USER_SSH_DIR.chmod(0o700)
    except PermissionError:
        pass
