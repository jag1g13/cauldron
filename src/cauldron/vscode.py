"""VSCode Remote-Containers integration."""

import shutil
import subprocess
import urllib.parse


def code_available():
    """Return True if the VSCode CLI (code) is on PATH."""
    return shutil.which("code") is not None


def build_remote_uri(container_name, workdir):
    """Build the vscode-remote URI for attaching to a running container.

    The URI scheme used is `vscode-remote://attached-container+<name><path>`,
    which opens the Remote-Containers extension's attach flow.
    """
    encoded_name = urllib.parse.quote(str(container_name), safe="-_.")
    encoded_path = urllib.parse.quote(str(workdir), safe="/")
    return f"vscode-remote://attached-container+{encoded_name}{encoded_path}"


def open_in_code(container_name, workdir):
    """Open a directory inside a container using the VSCode CLI.

    Returns the exit code from the code command.
    """
    uri = build_remote_uri(container_name, workdir)
    try:
        return subprocess.run(["code", "--folder-uri", uri]).returncode
    except FileNotFoundError:
        return 1
