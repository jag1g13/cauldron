"""End-to-end CLI tests against a real Podman service.

Skipped when no Podman is reachable. A project image is faked by tagging the
base image with the project's image name so ``up --no-build`` can start a
container without a full build.
"""

import pytest
from click.testing import CliRunner

from cauldron import podman
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
