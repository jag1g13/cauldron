from click.testing import CliRunner

from cauldron.cli import cli


def test_cli_without_command_prints_help():
    runner = CliRunner()
    result = runner.invoke(cli)
    assert result.exit_code == 0
    assert "Commands:" in result.output
    assert "check" in result.output


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
