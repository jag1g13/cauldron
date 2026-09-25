"""Behaviour tests that run against a real Podman service.

These are skipped automatically when no Podman is reachable (see
``podman_service`` in ``conftest.py``). They exercise the wrapper end to end
instead of asserting the exact command line.
"""

import pytest

from cauldron import podman

pytestmark = pytest.mark.integration


def test_version_reports_success(podman_service):
    assert podman.version() is True


def test_image_lifecycle(fast_image, scratch):
    assert podman.image_exists(fast_image) is True
    assert podman.image_id(fast_image)
    assert isinstance(podman.image_env(fast_image), dict)

    target = scratch.image("tagged")
    assert podman.tag(fast_image, target) is True
    assert podman.image_exists(target) is True


def test_image_env_reads_config_and_handles_missing(base_image):
    # A real image's JSON env config is parsed into a dict...
    assert podman.image_env(base_image).get("PATH")
    # ...and inspecting a missing image fails, which must read as empty.
    assert podman.image_id("cauldron-no-such-image:latest") is None
    assert podman.image_env("cauldron-no-such-image:latest") == {}


def test_pull_returns_false_for_missing_image(podman_service, scratch):
    missing = "docker.io/library/cauldron-does-not-exist:latest"
    assert podman.pull(missing) is False


def test_build_image(fast_image, scratch, tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(f"FROM {fast_image}\nRUN echo cauldron-build-ok\n")
    tag = scratch.image("built")

    assert podman.build_image(tag, dockerfile, tmp_path) is True
    assert podman.image_exists(tag) is True


def test_build_project_image(base_image, scratch):
    tag = scratch.image("project")
    assert podman.build_project_image(tag, "1000", "1000", base_image=base_image)
    assert podman.image_exists(tag) is True


def test_container_lifecycle(base_image, scratch, tmp_path):
    name = scratch.container()
    assert (
        podman.run_container(
            name=name,
            image=base_image,
            workdir=str(tmp_path),
            project_dir=str(tmp_path),
            uid="1000",
            gid="1000",
        )
        is True
    )

    assert podman.container_exists(name) is True
    assert podman.container_id(name)
    assert podman.container_running(name) is True

    assert podman.stop_container(name) is True
    assert podman.container_running(name) is False

    assert podman.start_container(name) is True
    assert podman.container_running(name) is True

    assert podman.restart_container(name) is True
    assert podman.container_running(name) is True

    assert podman.remove_container(name, force=True) is True
    assert podman.container_exists(name) is False
    # Inspecting a removed container fails, which must read as absent.
    assert podman.container_running(name) is False
    assert podman.container_id(name) is None


def test_list_containers_includes_labelled_container(base_image, scratch, tmp_path):
    name = scratch.container()
    podman.run_container(
        name=name,
        image=base_image,
        workdir=str(tmp_path),
        project_dir=str(tmp_path),
        uid="1000",
        gid="1000",
    )

    containers = podman.list_containers(all_containers=True)
    match = next((c for c in containers if c["name"] == name), None)
    assert match is not None
    assert match["project_dir"] == str(tmp_path)


def test_exec_helpers(base_image, scratch, tmp_path):
    name = scratch.container()
    podman.run_container(
        name=name,
        image=base_image,
        workdir=str(tmp_path),
        project_dir=str(tmp_path),
        uid="1000",
        gid="1000",
    )

    assert podman.exec_check(name, ["true"]) is True
    assert podman.exec_check(name, ["false"]) is False
    assert podman.exec_capture(name, ["echo", "cauldron"]) == "cauldron"
    assert podman.exec_with_stdin(name, ["cat"], "hello") is True
    assert podman.exec_hook_in_container(name, "true") == 0
    assert podman.exec_hook_in_container(name, "false") == 1
    assert podman.container_shell(name)


def test_run_container_applies_environment(base_image, scratch, tmp_path):
    name = scratch.container()
    podman.run_container(
        name=name,
        image=base_image,
        workdir=str(tmp_path),
        project_dir=str(tmp_path),
        uid="1000",
        gid="1000",
        env={"CAULDRON_TEST_VALUE": "hello"},
    )

    assert podman.exec_capture(name, ["printenv", "CAULDRON_TEST_VALUE"]) == "hello"
