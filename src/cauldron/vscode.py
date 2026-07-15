"""VSCode Remote-SSH integration."""

import shutil
import subprocess
import urllib.parse


def code_available():
    """Return True if the VSCode CLI (code) is on PATH."""
    return shutil.which("code") is not None


def build_remote_uri(host, workdir):
    """Build the vscode-remote URI for connecting via SSH.

    The authority is the SSH host name (matching a Host entry in the
    SSH config). The path is the project directory inside the container.
    """
    encoded_path = urllib.parse.quote(str(workdir), safe="/")
    return f"vscode-remote://ssh-remote+{host}{encoded_path}"


def open_in_code(host, workdir):
    """Open a directory on an SSH host using the VSCode CLI.

    Returns the exit code from the code command.
    """
    uri = build_remote_uri(host, workdir)
    try:
        return subprocess.run(["code", "--folder-uri", uri]).returncode
    except FileNotFoundError:
        return 1
