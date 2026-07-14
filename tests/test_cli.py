import pathlib
from unittest.mock import patch

from click.testing import CliRunner

from cauldron.cli import cli
from cauldron import podman as podman_module


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
        podman_module.ensure_base_image = lambda _base_image=None: True
        podman_module.run_test_container = lambda _base_image=None: True

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


@patch("cauldron.podman.container_exists", return_value=False)
def test_up_rejects_conflicting_build_flags(mock_exists):
    runner = CliRunner()
    result = runner.invoke(cli, ["up", "--build", "--no-build"])
    assert result.exit_code != 0
    assert "cannot be used together" in result.output


@patch("cauldron.podman.container_exists", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
@patch("cauldron.podman.restart_container", return_value=True)
def test_up_restarts_existing_running_container(
    mock_restart, mock_running, mock_exists
):
    runner = CliRunner()
    result = runner.invoke(cli, ["up"])
    assert result.exit_code == 0
    assert "Restarting container" in result.output
    mock_restart.assert_called_once()


@patch("cauldron.podman.container_exists", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
@patch("cauldron.podman.stop_container", return_value=True)
@patch("cauldron.podman.remove_container", return_value=True)
@patch("cauldron.podman.image_exists", return_value=True)
@patch("cauldron.podman.ensure_base_image", return_value=True)
@patch("cauldron.podman.build_project_image", return_value=True)
@patch("cauldron.podman.run_container", return_value=True)
@patch("cauldron.project.find_dockerfile", return_value=None)
def test_up_build_rebuilds_and_replaces_existing_container(
    mock_dockerfile,
    mock_run,
    mock_build,
    mock_base,
    mock_image,
    mock_remove,
    mock_stop,
    mock_running,
    mock_exists,
):
    runner = CliRunner()
    result = runner.invoke(cli, ["up", "--build"])
    assert result.exit_code == 0
    assert "Stopping and removing" in result.output
    mock_stop.assert_called_once()
    mock_remove.assert_called_once()
    mock_build.assert_called_once()
    mock_run.assert_called_once()


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
@patch("cauldron.podman.image_exists", return_value=False)
@patch("cauldron.podman.ensure_base_image", return_value=True)
@patch("cauldron.podman.build_project_image", return_value=True)
@patch("cauldron.podman.run_container", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
@patch("cauldron.project.find_dockerfile", return_value=None)
def test_up_uses_configured_base_image(
    mock_dockerfile,
    mock_running,
    mock_run,
    mock_build,
    mock_base,
    mock_image,
    mock_container,
    tmp_path,
):
    config_file = tmp_path / ".cauldron" / "cauldron.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("""
[container]
base_image = "astral/uv:python3.14-trixie"
""")

    with patch("cauldron.project.pathlib.Path.cwd", return_value=tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["up"])
        assert result.exit_code == 0
        mock_base.assert_called_once_with("astral/uv:python3.14-trixie")


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
    dockerfile.write_text("ARG CAULDRON_BASE\nFROM ${CAULDRON_BASE}\nRUN echo hi\n")

    with patch("cauldron.project.pathlib.Path.cwd", return_value=tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["up", "--build"])
        assert result.exit_code == 0
        mock_base.assert_called_once_with(podman_module.BASE_IMAGE)
        mock_build_image.assert_called_once()
        _, build_kwargs = mock_build_image.call_args
        assert build_kwargs.get("build_args") == {
            "CAULDRON_BASE": podman_module.BASE_IMAGE
        }
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


@patch("sys.stdin.isatty", return_value=True)
@patch("sys.stdout.isatty", return_value=True)
def test_tty_flags_detects_interactive_terminal(mock_stdout_tty, mock_stdin_tty):
    from cauldron.cli import _tty_flags

    assert _tty_flags() == (True, True)


@patch("sys.stdin.isatty", return_value=False)
@patch("sys.stdout.isatty", return_value=False)
def test_tty_flags_detects_non_interactive(mock_stdout_tty, mock_stdin_tty):
    from cauldron.cli import _tty_flags

    assert _tty_flags() == (False, False)


@patch("cauldron.podman.container_exists", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
@patch("cauldron.podman.exec_in_container", return_value=0)
def test_exec_runs_command_in_running_container(mock_exec, mock_running, mock_exists):
    runner = CliRunner()
    result = runner.invoke(cli, ["exec", "ls", "-la"])
    assert result.exit_code == 0
    mock_exec.assert_called_once()
    args, kwargs = mock_exec.call_args
    assert args[1] == "ls"
    assert kwargs["args"] == ("-la",)


@patch("cauldron.podman.container_exists", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
@patch("cauldron.podman.exec_in_container", return_value=42)
def test_exec_propagates_nonzero_exit_code(mock_exec, mock_running, mock_exists):
    runner = CliRunner()
    result = runner.invoke(cli, ["exec", "false"])
    assert result.exit_code == 42


@patch("cauldron.podman.container_exists", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
@patch("cauldron.podman.container_shell", return_value="/bin/zsh")
@patch("cauldron.podman.exec_in_container", return_value=0)
def test_exec_opens_default_shell_when_no_command(
    mock_exec, mock_shell, mock_running, mock_exists
):
    runner = CliRunner()
    result = runner.invoke(cli, ["exec"])
    assert result.exit_code == 0
    mock_shell.assert_called_once()
    args, kwargs = mock_exec.call_args
    assert args[1] == "/bin/zsh"


@patch("cauldron.podman.container_exists", return_value=False)
@patch("cauldron.podman.image_exists", return_value=True)
@patch("cauldron.podman.run_container", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
@patch("cauldron.podman.exec_in_container", return_value=0)
@patch("cauldron.project.find_dockerfile", return_value=None)
def test_exec_auto_starts_container_when_not_exists(
    mock_dockerfile,
    mock_exec,
    mock_running,
    mock_run,
    mock_image,
    mock_exists,
):
    runner = CliRunner()
    result = runner.invoke(cli, ["exec", "whoami"])
    assert result.exit_code == 0
    mock_run.assert_called_once()
    mock_exec.assert_called_once()


@patch("cauldron.podman.container_exists", return_value=True)
@patch("cauldron.podman.container_running", side_effect=[False, True])
@patch("cauldron.podman.start_container", return_value=True)
@patch("cauldron.podman.exec_in_container", return_value=0)
def test_exec_starts_container_when_stopped(
    mock_exec, mock_start, mock_running, mock_exists
):
    runner = CliRunner()
    result = runner.invoke(cli, ["exec", "pwd"])
    assert result.exit_code == 0
    mock_start.assert_called_once()
    mock_exec.assert_called_once()


@patch("cauldron.podman.container_exists", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
@patch("cauldron.vscode.code_available", return_value=False)
def test_code_fails_when_code_cli_missing(
    mock_code_available, mock_running, mock_exists
):
    runner = CliRunner()
    result = runner.invoke(cli, ["code"])
    assert result.exit_code != 0
    assert "VSCode CLI" in result.output


@patch("cauldron.podman.container_exists", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
@patch("cauldron.vscode.code_available", return_value=True)
@patch("cauldron.vscode.open_in_code", return_value=0)
def test_code_opens_project_in_vscode(
    mock_open, mock_code_available, mock_running, mock_exists
):
    runner = CliRunner()
    result = runner.invoke(cli, ["code"])
    assert result.exit_code == 0
    mock_open.assert_called_once()
    args, _kwargs = mock_open.call_args
    assert "cauldron-" in args[0]


@patch("cauldron.podman.container_exists", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
@patch("cauldron.vscode.code_available", return_value=True)
@patch("cauldron.vscode.open_in_code", return_value=1)
def test_code_propagates_nonzero_exit_code(
    mock_open, mock_code_available, mock_running, mock_exists
):
    runner = CliRunner()
    result = runner.invoke(cli, ["code"])
    assert result.exit_code == 1


@patch("cauldron.podman.container_exists", return_value=False)
@patch("cauldron.podman.image_exists", return_value=True)
@patch("cauldron.podman.run_container", return_value=True)
@patch("cauldron.podman.container_running", return_value=True)
@patch("cauldron.vscode.code_available", return_value=True)
@patch("cauldron.vscode.open_in_code", return_value=0)
@patch("cauldron.project.find_dockerfile", return_value=None)
def test_code_auto_starts_container_when_not_exists(
    mock_dockerfile,
    mock_open,
    mock_code_available,
    mock_running,
    mock_run,
    mock_image,
    mock_exists,
):
    runner = CliRunner()
    result = runner.invoke(cli, ["code"])
    assert result.exit_code == 0
    mock_run.assert_called_once()
    mock_open.assert_called_once()


def test_init_creates_cauldron_directory_and_templates():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "Created .cauldron/Dockerfile" in result.output
        assert "Created .cauldron/cauldron.toml" in result.output

        dockerfile = pathlib.Path(".cauldron/Dockerfile")
        assert dockerfile.exists()
        content = dockerfile.read_text()
        assert "ARG CAULDRON_BASE" in content
        assert "FROM ${CAULDRON_BASE}" in content

        config_file = pathlib.Path(".cauldron/cauldron.toml")
        assert config_file.exists()
        content = config_file.read_text()
        assert "[env]" in content
        assert "mounts" in content
        assert "ports" in content


def test_init_keeps_existing_files():
    runner = CliRunner()
    with runner.isolated_filesystem():
        pathlib.Path(".cauldron").mkdir()
        pathlib.Path(".cauldron/Dockerfile").write_text("FROM custom\n")
        pathlib.Path(".cauldron/cauldron.toml").write_text('[env]\nFOO = "bar"\n')

        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "Keeping existing .cauldron/Dockerfile" in result.output
        assert "Keeping existing .cauldron/cauldron.toml" in result.output
        assert pathlib.Path(".cauldron/Dockerfile").read_text() == "FROM custom\n"
        assert (
            pathlib.Path(".cauldron/cauldron.toml").read_text()
            == '[env]\nFOO = "bar"\n'
        )
