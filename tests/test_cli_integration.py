"""End-to-end CLI tests against a real Podman service.

Skipped when no Podman is reachable. A project image is faked by tagging the
base image with the project's image name so ``up --no-build`` can start a
container without a full build.
"""

import pathlib
import shutil
import uuid

import pytest
from click.testing import CliRunner

from cauldron import podman, podman_remote
from cauldron.cli import cli

pytestmark = pytest.mark.integration


@pytest.fixture
def project(base_image, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    name = tmp_path.name
    image = f"cauldron-{name}:latest"
    container = f"cauldron-{name}"
    assert podman.tag(base_image, image) is True
    yield {"image": image, "container": container}
    podman.remove_container(container, force=True)
    podman._run(["rmi", "-f", image])


def invoke(args):
    return CliRunner().invoke(cli, args)


def test_up_exec_ps_stop_rm(project):
    result = invoke(["up", "--no-build"])
    assert result.exit_code == 0
    assert "is running" in result.output
    assert podman.container_exists(project["container"]) is True

    result = invoke(["exec", "echo", "cauldron-e2e"])
    assert result.exit_code == 0

    result = invoke(["ps"])
    assert result.exit_code == 0
    assert project["container"] in result.output

    result = invoke(["stop"])
    assert result.exit_code == 0
    assert podman.container_running(project["container"]) is False

    result = invoke(["rm", "--force"])
    assert result.exit_code == 0
    assert podman.container_exists(project["container"]) is False


def test_exec_propagates_exit_code(project):
    assert invoke(["up", "--no-build"]).exit_code == 0
    result = invoke(["exec", "false"])
    assert result.exit_code == 1


def test_up_nested_creates_host_sibling(podman_service, base_image, monkeypatch):
    if not podman_remote.running_in_remote():
        pytest.skip("not running inside a cauldron using the host socket")

    # The sibling's project dir must be host-visible, so create it under the
    # current (host-mounted) project directory and clean it up afterwards. The
    # name is a clean slug (no leading dot) so the derived image tag is valid.
    workdir = pathlib.Path.cwd() / f"cauldron-nested-e2e-{uuid.uuid4().hex[:8]}"
    (workdir / ".cauldron").mkdir(parents=True)
    (workdir / ".cauldron" / "cauldron.toml").write_text(
        "[container]\npodman_socket = true\n"
    )
    container = f"cauldron-{workdir.name}"
    image = f"cauldron-{workdir.name}:latest"
    assert podman.tag(base_image, image) is True
    monkeypatch.chdir(workdir)
    try:
        result = invoke(["up", "--no-build"])
        assert result.exit_code == 0, result.output
        assert podman.container_running(container) is True

        destinations = {
            mount["Destination"] for mount in podman.container_mounts(container)
        }
        assert "/run/podman-host" in destinations
        assert "/usr/local/bin/cauldron-podman" in destinations

        # The sibling reaches the host's Podman through the mounted client.
        assert podman.exec_capture(
            container,
            ["/bin/sh", "-c", "HOME=/tmp /usr/local/bin/cauldron-podman version"],
        )
    finally:
        podman.remove_container(container, force=True)
        podman._run(["rmi", "-f", image])
        shutil.rmtree(workdir, ignore_errors=True)
