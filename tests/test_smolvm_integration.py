"""Real-smolvm integration tests (slow).

Skipped automatically unless a real ``smolvm`` binary is resolvable and
``/dev/kvm`` is writable for this user; the default suite deselects them
(``-m not slow``). Mirrors ``test_podman_integration.py``: exercise the
wrapper end to end against a real machine instead of asserting exact command
lines. Runs both on a host and inside a cauldron (pytest's ``--basetemp``
sits inside the project, so the host builder and virtiofs see the same
absolute paths).

Run with:

    uv run pytest -m slow tests/test_smolvm_integration.py
"""

import os
import shutil
import socket
import uuid

import pytest

from cauldron import smolvm

BASE_IMAGE = "docker.io/library/debian:trixie-slim"


def _smolvm_binary():
    """Return the resolvable smolvm path, or None if there is no real one."""
    candidate = os.environ.get("CAULDRON_SMOLVM_PATH", "smolvm")
    resolved = shutil.which(candidate) or candidate
    if os.path.isfile(resolved) and os.access(resolved, os.X_OK):
        return resolved
    return None


def _kvm_usable():
    return os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.W_OK)


pytestmark = [
    pytest.mark.slow,
    pytest.mark.integration,
    pytest.mark.skipif(_smolvm_binary() is None, reason="no real smolvm binary found"),
    pytest.mark.skipif(not _kvm_usable(), reason="/dev/kvm is missing or not writable"),
]


@pytest.fixture(scope="session")
def project_image(tmp_path_factory):
    """Build a tiny project image once and export its docker-save tar.

    The tar directory is session-local so the real cache is not disturbed.
    """
    previous = os.environ.get("CAULDRON_SMOLTAR_DIR")
    os.environ["CAULDRON_SMOLTAR_DIR"] = str(tmp_path_factory.mktemp("smoltar"))
    project_dir = tmp_path_factory.mktemp("slow-project")
    tag = f"cauldron-slow:{uuid.uuid4().hex[:8]}"
    try:
        result = smolvm.build_project_image(
            tag,
            "1000",
            "1000",
            base_image=BASE_IMAGE,
            project_dir=str(project_dir),
        )
        assert result.returncode == 0, result.stderr
        assert smolvm.image_exists(tag) is True
        yield tag
    finally:
        if previous is None:
            os.environ.pop("CAULDRON_SMOLTAR_DIR", None)
        else:
            os.environ["CAULDRON_SMOLTAR_DIR"] = previous


@pytest.fixture
def machine_name():
    """A unique machine name, force-removed afterwards even on failure."""
    name = f"cauldron-slow-{uuid.uuid4().hex[:8]}"
    yield name
    smolvm.remove_container(name)


def _owner_ids(path):
    """Return the temp dir owner's (uid, gid) as strings (keep-id mapping)."""
    st = os.stat(path)
    return str(st.st_uid), str(st.st_gid)


def test_machine_roundtrip(project_image, machine_name, tmp_path):
    uid, gid = _owner_ids(tmp_path)
    result = smolvm.run_container(
        name=machine_name,
        image=project_image,
        workdir=str(tmp_path),
        project_dir=str(tmp_path),
        uid=uid,
        gid=gid,
    )
    assert result.returncode == 0, result.stderr
    # machine ls parsing agrees the machine exists and is running
    assert smolvm.container_exists(machine_name) is True
    assert smolvm.container_running(machine_name) is True

    # exec runs as the keep-id user inside the mounted project dir
    assert smolvm.exec_check(machine_name, ["true"]) is True
    whoami = smolvm.exec_capture(machine_name, ["id"])
    assert f"uid={uid}" in whoami

    # stop, status reflects it, restart, then remove
    assert smolvm.stop_container(machine_name) is True
    assert smolvm.container_running(machine_name) is False
    assert smolvm.start_container(machine_name).returncode == 0
    assert smolvm.container_running(machine_name) is True
    assert smolvm.remove_container(machine_name) is True
    assert smolvm.container_exists(machine_name) is False


def test_port_conflict_error_shape(project_image, machine_name, tmp_path):
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        uid, gid = _owner_ids(tmp_path)
        result = smolvm.run_container(
            name=machine_name,
            image=project_image,
            workdir=str(tmp_path),
            project_dir=str(tmp_path),
            uid=uid,
            gid=gid,
            ports=[f"{port}:3000"],
        )
        assert result.returncode != 0
        stderr = result.stderr or ""
        assert smolvm.is_port_in_use_error(stderr) is True
        # the host port, not the guest port the message also mentions
        assert smolvm.port_from_error(stderr) == port
    finally:
        holder.close()
