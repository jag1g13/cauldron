from unittest.mock import patch

from click.testing import CliRunner

from cauldron.cli import cli


def test_cli_without_command_prints_help():
    runner = CliRunner()
    result = runner.invoke(cli)
    assert result.exit_code == 0
    assert "Commands:" in result.output
    assert "check" in result.output


def test_verbose_flag_enables_podman_verbose():
    runner = CliRunner()
    import cauldron.podman as podman_module

    original_verbose = podman_module._verbose
    try:
        result = runner.invoke(cli, ["--verbose"])
        assert result.exit_code == 0
        assert podman_module._verbose is True
    finally:
        podman_module._verbose = original_verbose


def test_check_succeeds_when_all_steps_pass():
    runner = CliRunner()
    with runner.isolated_filesystem():
        # Patch podman functions used by check
        import cauldron.podman as podman_module

        original_version = podman_module.version
        original_ensure_base_image = podman_module.ensure_base_image
        original_run_test_container = podman_module.run_test_container

        podman_module.version = lambda: True
        podman_module.ensure_base_image = lambda: True
        podman_module.run_test_container = lambda: True

        try:
            result = runner.invoke(cli, ["check"])
            assert result.exit_code == 0
            assert "All checks passed." in result.output
        finally:
            podman_module.version = original_version
            podman_module.ensure_base_image = original_ensure_base_image
            podman_module.run_test_container = original_run_test_container


def test_check_fails_when_podman_not_installed():
    runner = CliRunner()
    import cauldron.podman as podman_module

    original_version = podman_module.version
    podman_module.version = lambda: False

    try:
        result = runner.invoke(cli, ["check"])
        assert result.exit_code != 0
        assert "Check failed: podman is installed" in result.output
    finally:
        podman_module.version = original_version


def test_up_rejects_conflicting_build_flags():
    runner = CliRunner()
    result = runner.invoke(cli, ["up", "--build", "--no-build"])
    assert result.exit_code != 0
    assert "cannot be used together" in result.output


@patch("cauldron.podman.container_exists", return_value=True)
def test_up_fails_when_container_already_exists(mock_exists):
    runner = CliRunner()
    result = runner.invoke(cli, ["up"])
    assert result.exit_code != 0
    assert "already exists" in result.output


@patch("cauldron.podman.container_exists", return_value=False)
@patch("cauldron.podman.image_exists", return_value=False)
def test_up_no_build_fails_when_image_missing(mock_image, mock_container):
    runner = CliRunner()
    result = runner.invoke(cli, ["up", "--no-build"])
    assert result.exit_code != 0
    assert "missing" in result.output


@patch("cauldron.podman.container_exists", return_value=False)
@patch("cauldron.podman.image_exists", return_value=False)
@patch("cauldron.podman.ensure_base_image", return_value=True)
@patch("cauldron.podman.build_project_image", return_value=True)
@patch("cauldron.podman.run_container", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
@patch("cauldron.project.find_dockerfile", return_value=None)
def test_up_builds_and_starts_container_when_image_missing(
    mock_dockerfile,
    mock_running,
    mock_run,
    mock_build,
    mock_base,
    mock_image,
    mock_container,
):
    runner = CliRunner()
    result = runner.invoke(cli, ["up"])
    assert result.exit_code == 0
    assert "is running" in result.output
    mock_build.assert_called_once()
    mock_run.assert_called_once()


@patch("cauldron.podman.container_exists", return_value=False)
@patch("cauldron.podman.image_exists", return_value=True)
@patch("cauldron.podman.build_project_image")
@patch("cauldron.podman.run_container", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
@patch("cauldron.project.find_dockerfile", return_value=None)
def test_up_skips_build_when_image_exists(
    mock_dockerfile, mock_running, mock_run, mock_build, mock_image, mock_container
):
    runner = CliRunner()
    result = runner.invoke(cli, ["up"])
    assert result.exit_code == 0
    mock_build.assert_not_called()


@patch("cauldron.podman.container_exists", return_value=False)
@patch("cauldron.podman.image_exists", return_value=True)
@patch("cauldron.podman.ensure_base_image", return_value=True)
@patch("cauldron.podman.build_image", return_value=True)
@patch("cauldron.podman.build_project_image", return_value=True)
@patch("cauldron.podman.run_container", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
def test_up_builds_intermediate_when_dockerfile_exists(
    mock_running,
    mock_run,
    mock_project_build,
    mock_build_image,
    mock_base,
    mock_image,
    mock_container,
    tmp_path,
):
    dockerfile = tmp_path / ".cauldron" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM cauldron-base\nRUN echo hi\n")

    with patch("cauldron.project.pathlib.Path.cwd", return_value=tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["up", "--build"])
        assert result.exit_code == 0
        mock_build_image.assert_called_once()
        mock_project_build.assert_called_once()


@patch("cauldron.podman.container_exists", return_value=False)
def test_stop_fails_when_container_missing(mock_exists):
    runner = CliRunner()
    result = runner.invoke(cli, ["stop"])
    assert result.exit_code != 0
    assert "does not exist" in result.output


@patch("cauldron.podman.container_exists", return_value=True)
@patch("cauldron.podman.stop_container", return_value=True)
def test_stop_stops_container(mock_stop, mock_exists):
    runner = CliRunner()
    result = runner.invoke(cli, ["stop"])
    assert result.exit_code == 0
    assert "stopped" in result.output
    mock_stop.assert_called_once()


@patch("cauldron.podman.container_exists", return_value=False)
def test_rm_fails_when_container_missing(mock_exists):
    runner = CliRunner()
    result = runner.invoke(cli, ["rm"])
    assert result.exit_code != 0
    assert "does not exist" in result.output


@patch("cauldron.podman.container_exists", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
def test_rm_fails_when_container_running_without_force(mock_running, mock_exists):
    runner = CliRunner()
    result = runner.invoke(cli, ["rm"])
    assert result.exit_code != 0
    assert "is running" in result.output


@patch("cauldron.podman.container_exists", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
@patch("cauldron.podman.remove_container", return_value=True)
def test_rm_force_stops_and_removes(mock_remove, mock_running, mock_exists):
    runner = CliRunner()
    result = runner.invoke(cli, ["rm", "--force"])
    assert result.exit_code == 0
    assert "removed" in result.output
    mock_remove.assert_called_once()
    assert mock_remove.call_args.kwargs["force"] is True


@patch("cauldron.podman.list_containers", return_value=[])
def test_ps_shows_no_containers_message(mock_list):
    runner = CliRunner()
    result = runner.invoke(cli, ["ps"])
    assert result.exit_code == 0
    assert "No Cauldron containers" in result.output


@patch(
    "cauldron.podman.list_containers",
    return_value=[
        {
            "name": "cauldron-foo",
            "image": "cauldron-foo:latest",
            "status": "running",
            "project_dir": "/home/deck/projects/foo",
        }
    ],
)
def test_ps_lists_containers(mock_list):
    runner = CliRunner()
    result = runner.invoke(cli, ["ps"])
    assert result.exit_code == 0
    assert "cauldron-foo" in result.output
    assert "/home/deck/projects/foo" in result.output
