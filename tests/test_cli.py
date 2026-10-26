"""CLI tests driven through the recording fake podman executable.

Running the whole CLI against the fake exercises the real command construction
and control flow without in-process mocks of ``cauldron.podman``. Tests that
need a live engine live in ``test_cli_integration.py``.
"""

import pathlib
import subprocess
from contextlib import ExitStack
from unittest.mock import patch

from click.testing import CliRunner

from cauldron import podman_remote
from cauldron import ssh
from cauldron.cli import _tty_flags, cli


def invoke(args):
    return CliRunner().invoke(cli, args)


def containers_exist(fake, returncode=0):
    fake.respond(["container", "exists"], returncode=returncode)


def container_is_running(fake, running=True):
    fake.respond(["container", "inspect"], stdout="true\n" if running else "false\n")


def build_calls(fake):
    return [call for call in fake.calls() if call and call[0] == "build"]


def _ok():
    """A successful build result (CompletedProcess with returncode 0)."""
    return subprocess.CompletedProcess(args=[], returncode=0)


def _failed(stderr):
    """A failed build result (CompletedProcess with returncode 1)."""
    return subprocess.CompletedProcess(args=[], returncode=1, stderr=stderr)


TEMPLATE_STDERR = (
    "Error: error creating build container: error preparing image configuration: "
    "error converting image ... Unknown media type during manifest conversion: "
    '"application/vnd.devcontainers.layer.v1+tar"'
)
TEMPLATE_REF = "ghcr.io/devcontainers/templates/typescript-node:5.0.0"
MCR_REF = "mcr.microsoft.com/devcontainers/typescript-node:5.0-24"


def _write_template_project(tmp_path):
    """Create a project dir with a template base_image and a Dockerfile."""
    cauldron_dir = tmp_path / ".cauldron"
    cauldron_dir.mkdir()
    (cauldron_dir / "cauldron.toml").write_text(
        f'[container]\nbase_image = "{TEMPLATE_REF}"\n'
    )
    (cauldron_dir / "Dockerfile").write_text(
        "ARG CAULDRON_BASE\nFROM ${CAULDRON_BASE}\nRUN echo hi\n"
    )


def _common_up_patches(tmp_path):
    """Return context managers patching the non-build up() dependencies."""
    return (
        patch("cauldron.podman.container_exists", return_value=False),
        patch("cauldron.podman.image_exists", return_value=False),
        patch("cauldron.podman.ensure_base_image", return_value=True),
        patch("cauldron.podman.run_container", return_value=True),
        patch("cauldron.podman.container_running", return_value=True),
        patch("cauldron.project.pathlib.Path.cwd", return_value=tmp_path),
        patch("cauldron.config.CONFIG_GLOBAL_PATH", tmp_path / "missing.toml"),
    )


def _invoke_up(
    tmp_path,
    build_image_patch=None,
    build_project_patch=None,
    resolve=None,
    args=None,
    input=None,
):
    """Run `cauldron up --build` with standard patches and return the result."""
    with ExitStack() as stack:
        for p in _common_up_patches(tmp_path):
            stack.enter_context(p)
        if build_image_patch is not None:
            stack.enter_context(build_image_patch)
        if build_project_patch is not None:
            stack.enter_context(build_project_patch)
        if resolve is not None:
            stack.enter_context(resolve)
        runner = CliRunner()
        return runner.invoke(cli, args or ["up", "--build"], input=input)


def test_up_recovers_template_base_intermediate(tmp_path):
    _write_template_project(tmp_path)

    calls = {"n": 0}

    def build_side_effect(*a, **k):
        calls["n"] += 1
        return _failed(TEMPLATE_STDERR) if calls["n"] == 1 else _ok()

    result = _invoke_up(
        tmp_path,
        build_image_patch=patch(
            "cauldron.podman.build_image", side_effect=build_side_effect
        ),
        build_project_patch=patch(
            "cauldron.podman.build_project_image", return_value=_ok()
        ),
        resolve=patch("cauldron.devcontainers.resolve_mcr_image", return_value=MCR_REF),
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert "Suggested replacement" in result.output
    assert "Updated base_image" in result.output
    assert calls["n"] == 2
    content = (tmp_path / ".cauldron" / "cauldron.toml").read_text()
    assert MCR_REF in content
    assert TEMPLATE_REF not in content


def test_up_recovers_template_base_without_dockerfile(tmp_path):
    cauldron_dir = tmp_path / ".cauldron"
    cauldron_dir.mkdir()
    (cauldron_dir / "cauldron.toml").write_text(
        f'[container]\nbase_image = "{TEMPLATE_REF}"\n'
    )

    calls = {"n": 0}

    def build_side_effect(*a, **k):
        calls["n"] += 1
        return _failed(TEMPLATE_STDERR) if calls["n"] == 1 else _ok()

    result = _invoke_up(
        tmp_path,
        build_project_patch=patch(
            "cauldron.podman.build_project_image", side_effect=build_side_effect
        ),
        resolve=patch("cauldron.devcontainers.resolve_mcr_image", return_value=MCR_REF),
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert "Updated base_image" in result.output
    assert calls["n"] == 2
    content = (tmp_path / ".cauldron" / "cauldron.toml").read_text()
    assert MCR_REF in content


def test_up_template_recovery_declined(tmp_path):
    _write_template_project(tmp_path)

    result = _invoke_up(
        tmp_path,
        build_image_patch=patch(
            "cauldron.podman.build_image", return_value=_failed(TEMPLATE_STDERR)
        ),
        build_project_patch=patch(
            "cauldron.podman.build_project_image", return_value=_ok()
        ),
        resolve=patch("cauldron.devcontainers.resolve_mcr_image", return_value=MCR_REF),
        input="n\n",
    )

    assert result.exit_code != 0
    assert "Failed to build intermediate image" in result.output
    content = (tmp_path / ".cauldron" / "cauldron.toml").read_text()
    assert TEMPLATE_REF in content
    assert MCR_REF not in content


def test_up_template_recovery_no_suggestion(tmp_path):
    _write_template_project(tmp_path)

    result = _invoke_up(
        tmp_path,
        build_image_patch=patch(
            "cauldron.podman.build_image", return_value=_failed(TEMPLATE_STDERR)
        ),
        build_project_patch=patch(
            "cauldron.podman.build_project_image", return_value=_ok()
        ),
        resolve=patch("cauldron.devcontainers.resolve_mcr_image", return_value=None),
        input="y\n",
    )

    assert result.exit_code != 0
    assert "Could not verify" in result.output
    assert "Failed to build intermediate image" in result.output
    content = (tmp_path / ".cauldron" / "cauldron.toml").read_text()
    assert TEMPLATE_REF in content


def test_up_non_template_build_failure_is_not_recovered(tmp_path):
    cauldron_dir = tmp_path / ".cauldron"
    cauldron_dir.mkdir()
    (cauldron_dir / "Dockerfile").write_text(
        "ARG CAULDRON_BASE\nFROM ${CAULDRON_BASE}\nRUN echo hi\n"
    )
    # No base_image in config -> default Debian base (not a template).

    result = _invoke_up(
        tmp_path,
        build_image_patch=patch(
            "cauldron.podman.build_image",
            return_value=_failed("Error: something else went wrong"),
        ),
        build_project_patch=patch(
            "cauldron.podman.build_project_image", return_value=_ok()
        ),
        input="y\n",
    )

    assert result.exit_code != 0
    assert "Failed to build intermediate image" in result.output
    assert "Suggested replacement" not in result.output
    assert "something else went wrong" in result.output


def test_cli_without_command_prints_help():
    result = invoke([])
    assert result.exit_code == 0
    assert "Commands:" in result.output
    assert "check" in result.output


def test_verbose_flag_enables_podman_verbose():
    import cauldron.podman as podman_module

    result = invoke(["--verbose"])
    assert result.exit_code == 0
    assert podman_module._verbose is True


def test_check_succeeds_when_all_steps_pass(fake_podman):
    result = invoke(["check"])
    assert result.exit_code == 0
    assert "All checks passed." in result.output


def test_check_fails_when_podman_not_installed(fake_podman):
    fake_podman.respond(["version"], returncode=1)
    result = invoke(["check"])
    assert result.exit_code != 0
    assert "Check failed: podman is installed" in result.output


def test_check_skips_socket_check_when_disabled(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = invoke(["check"])
    assert result.exit_code == 0
    assert "host Podman socket" not in result.output


def test_check_reports_not_running_in_cauldron(fake_podman, monkeypatch):
    with patch("cauldron.podman_remote.running_in_remote", return_value=False):
        result = invoke(["check"])
    assert result.exit_code == 0
    assert "Not running inside a cauldron." in result.output


def test_check_reports_running_in_cauldron(fake_podman, monkeypatch):
    with patch("cauldron.podman_remote.running_in_remote", return_value=True):
        result = invoke(["check"])
    assert result.exit_code == 0
    assert "Running inside an existing cauldron" in result.output


def test_check_reports_socket_when_available(fake_podman, monkeypatch, tmp_path):
    cauldron_dir = tmp_path / ".cauldron"
    cauldron_dir.mkdir()
    (cauldron_dir / "cauldron.toml").write_text("[container]\npodman_socket = true\n")
    monkeypatch.chdir(tmp_path)

    with (
        patch("cauldron.podman_remote.running_in_remote", return_value=False),
        patch(
            "cauldron.podman_remote.ensure_socket",
            return_value="/run/user/1000/podman/podman.sock",
        ),
    ):
        result = invoke(["check"])

    assert result.exit_code == 0
    assert "Checking host Podman socket... ok" in result.output
    assert "All checks passed." in result.output


def test_check_warns_but_passes_when_socket_unavailable(
    fake_podman, monkeypatch, tmp_path
):
    cauldron_dir = tmp_path / ".cauldron"
    cauldron_dir.mkdir()
    (cauldron_dir / "cauldron.toml").write_text("[container]\npodman_socket = true\n")
    monkeypatch.chdir(tmp_path)

    with (
        patch("cauldron.podman_remote.running_in_remote", return_value=False),
        patch(
            "cauldron.podman_remote.ensure_socket",
            side_effect=podman_remote.RemoteError("no socket"),
        ),
    ):
        result = invoke(["check"])

    assert result.exit_code == 0
    assert "Checking host Podman socket... unavailable" in result.output
    assert "warning: no socket" in result.output
    assert "All checks passed." in result.output


def test_up_rejects_conflicting_build_flags():
    result = invoke(["up", "--build", "--no-build"])
    assert result.exit_code != 0
    assert "cannot be used together" in result.output


def test_up_restarts_existing_running_container(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    container_is_running(fake_podman)

    result = invoke(["up"])

    assert result.exit_code == 0
    assert "Restarting container" in result.output
    assert any(call[0] == "restart" for call in fake_podman.calls())


def test_up_build_rebuilds_and_replaces_existing_container(
    fake_podman, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    container_is_running(fake_podman)

    result = invoke(["up", "--build"])

    assert result.exit_code == 0
    assert "Stopping and removing" in result.output
    calls = fake_podman.calls()
    assert any(call[0] == "stop" for call in calls)
    assert any(call[0] == "rm" for call in calls)
    assert build_calls(fake_podman)
    assert any(call[0] == "run" for call in calls)


def test_up_no_build_fails_when_image_missing(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)
    fake_podman.respond(["image", "exists"], returncode=1)

    result = invoke(["up", "--no-build"])

    assert result.exit_code != 0
    assert "missing" in result.output


def test_up_builds_and_starts_container_when_image_missing(
    fake_podman, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)
    fake_podman.respond(["image", "exists"], returncode=1)
    container_is_running(fake_podman)

    result = invoke(["up"])

    assert result.exit_code == 0
    assert "is running" in result.output
    assert build_calls(fake_podman)
    assert any(call[0] == "run" for call in fake_podman.calls())


def test_up_uses_configured_base_image(fake_podman, monkeypatch, tmp_path):
    (tmp_path / ".cauldron").mkdir()
    (tmp_path / ".cauldron" / "cauldron.toml").write_text(
        '[container]\nbase_image = "astral/uv:python3.14-trixie"\n'
    )
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)
    fake_podman.respond(["image", "exists"], returncode=1)
    container_is_running(fake_podman)

    result = invoke(["up"])

    assert result.exit_code == 0
    assert any(
        "CAULDRON_BASE=astral/uv:python3.14-trixie" in call
        for call in fake_podman.calls()
    )


def test_up_skips_build_when_image_exists(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)
    fake_podman.respond(["image", "exists"], returncode=0)
    container_is_running(fake_podman)

    result = invoke(["up"])

    assert result.exit_code == 0
    assert build_calls(fake_podman) == []


def test_up_builds_intermediate_when_dockerfile_exists(
    fake_podman, monkeypatch, tmp_path
):
    dockerfile = tmp_path / ".cauldron" / "Dockerfile"
    dockerfile.parent.mkdir()
    dockerfile.write_text("ARG CAULDRON_BASE\nFROM ${CAULDRON_BASE}\nRUN echo hi\n")
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)
    fake_podman.respond(["image", "exists"], returncode=1)
    container_is_running(fake_podman)

    result = invoke(["up", "--build"])

    assert result.exit_code == 0
    builds = build_calls(fake_podman)
    assert len(builds) == 2
    assert any(any("-intermediate:latest" in arg for arg in call) for call in builds)


def test_stop_fails_when_container_missing(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)

    result = invoke(["stop"])

    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_stop_stops_container(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)

    result = invoke(["stop"])

    assert result.exit_code == 0
    assert "stopped" in result.output
    assert any(call[0] == "stop" for call in fake_podman.calls())


def test_rm_fails_when_container_missing(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)

    result = invoke(["rm"])

    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_rm_fails_when_container_running_without_force(
    fake_podman, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    container_is_running(fake_podman)

    result = invoke(["rm"])

    assert result.exit_code != 0
    assert "is running" in result.output


def test_rm_force_removes(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    container_is_running(fake_podman)

    result = invoke(["rm", "--force"])

    assert result.exit_code == 0
    assert "removed" in result.output
    rm_calls = [call for call in fake_podman.calls() if call[0] == "rm"]
    assert rm_calls and "-f" in rm_calls[0]


def test_ps_shows_no_containers_message(fake_podman):
    fake_podman.respond(["ps"], stdout="[]")

    result = invoke(["ps"])

    assert result.exit_code == 0
    assert "No Cauldron containers" in result.output


def test_ps_lists_containers(fake_podman):
    fake_podman.respond(
        ["ps"],
        stdout=(
            '[{"Names": ["cauldron-foo"], "Image": "cauldron-foo:latest", '
            '"State": "running", '
            '"Ports": [{"host_ip": "0.0.0.0", "host_port": 2222, '
            '"container_port": 22, "protocol": "tcp"}], '
            '"Labels": {"cauldron.project_dir": "/home/deck/projects/foo"}}]'
        ),
    )

    result = invoke(["ps"])

    assert result.exit_code == 0
    assert "cauldron-foo" in result.output
    assert "0.0.0.0:2222->22/tcp" in result.output
    assert "/home/deck/projects/foo" in result.output


def test_ps_shows_placeholder_for_containers_without_ports(fake_podman):
    fake_podman.respond(
        ["ps"],
        stdout=(
            '[{"Names": ["cauldron-foo"], "Image": "cauldron-foo:latest", '
            '"State": "running", '
            '"Labels": {"cauldron.project_dir": "/home/deck/projects/foo"}}]'
        ),
    )

    result = invoke(["ps"])

    assert result.exit_code == 0
    assert "PORTS" in result.output.splitlines()[0]
    assert "-" in result.output


def test_tty_flags_detects_interactive_terminal():
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("sys.stdout.isatty", return_value=True),
    ):
        assert _tty_flags() == (True, True)


def test_tty_flags_detects_non_interactive():
    with (
        patch("sys.stdin.isatty", return_value=False),
        patch("sys.stdout.isatty", return_value=False),
    ):
        assert _tty_flags() == (False, False)


def test_exec_runs_command_in_running_container(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    container_is_running(fake_podman)

    result = invoke(["exec", "ls", "-la"])

    assert result.exit_code == 0
    exec_calls = [call for call in fake_podman.calls() if call[0] == "exec"]
    assert exec_calls[-1][-2:] == ["ls", "-la"]


def test_exec_options_enable_interactive_without_tty(
    fake_podman, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    container_is_running(fake_podman)

    result = invoke(["exec", "--interactive", "--no-tty", "opencode", "acp"])

    assert result.exit_code == 0
    exec_calls = [call for call in fake_podman.calls() if call[0] == "exec"]
    assert "-i" in exec_calls[-1]
    assert "-t" not in exec_calls[-1]
    assert exec_calls[-1][-2:] == ["opencode", "acp"]


def test_exec_options_disable_interactive_and_tty(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    container_is_running(fake_podman)

    result = invoke(["exec", "--no-interactive", "--no-tty", "sh"])

    assert result.exit_code == 0
    exec_calls = [call for call in fake_podman.calls() if call[0] == "exec"]
    assert "-i" not in exec_calls[-1]
    assert "-t" not in exec_calls[-1]


def test_exec_propagates_nonzero_exit_code(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    container_is_running(fake_podman)
    fake_podman.respond(["exec"], returncode=42)

    result = invoke(["exec", "false"])

    assert result.exit_code == 42


def test_exec_opens_default_shell_when_no_command(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    container_is_running(fake_podman)
    fake_podman.respond(["exec"], stdout="/bin/zsh\n")

    result = invoke(["exec"])

    assert result.exit_code == 0
    exec_calls = [call for call in fake_podman.calls() if call[0] == "exec"]
    assert exec_calls[0][-2:] == ["printenv", "SHELL"]
    assert exec_calls[1][-1] == "/bin/zsh"


def test_exec_auto_starts_container_when_not_exists(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)
    fake_podman.respond(["image", "exists"], returncode=0)
    container_is_running(fake_podman)

    result = invoke(["exec", "whoami"])

    assert result.exit_code == 0
    calls = fake_podman.calls()
    assert any(call[0] == "run" for call in calls)
    assert any(call[0] == "exec" for call in calls)


def test_exec_starts_container_when_stopped(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    fake_podman.respond(["container", "inspect"], stdout="false\n", times=1)
    container_is_running(fake_podman)

    result = invoke(["exec", "pwd"])

    assert result.exit_code == 0
    assert any(call[0] == "start" for call in fake_podman.calls())
    assert any(call[0] == "exec" for call in fake_podman.calls())


def test_up_runs_post_start_but_not_post_build_in_container(
    fake_podman, monkeypatch, tmp_path
):
    cauldron_dir = tmp_path / ".cauldron"
    cauldron_dir.mkdir()
    (cauldron_dir / "cauldron.toml").write_text(
        '[scripts]\npost_build = "post_build.sh"\npost_start = "post-start.sh"\n'
    )
    (cauldron_dir / "post_build.sh").write_text("#!/bin/bash\necho build\n")
    (cauldron_dir / "post-start.sh").write_text("#!/bin/bash\necho start\n")
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)
    fake_podman.respond(["image", "exists"], returncode=1)
    container_is_running(fake_podman)

    result = invoke(["up"])

    assert result.exit_code == 0
    exec_calls = [call for call in fake_podman.calls() if call[0] == "exec"]
    assert len(exec_calls) == 1
    assert exec_calls[0][-1] == "/usr/local/share/cauldron/post_start.sh"


def test_exec_runs_post_start_when_starting_stopped_container(
    fake_podman, monkeypatch, tmp_path
):
    cauldron_dir = tmp_path / ".cauldron"
    cauldron_dir.mkdir()
    (cauldron_dir / "cauldron.toml").write_text('[scripts]\npost_start = "x.sh"\n')
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    fake_podman.respond(["container", "inspect"], stdout="false\n", times=1)
    container_is_running(fake_podman)

    result = invoke(["exec", "whoami"])

    assert result.exit_code == 0
    assert any(call[0] == "start" for call in fake_podman.calls())
    exec_calls = [call for call in fake_podman.calls() if call[0] == "exec"]
    assert any(
        call[-1] == "/usr/local/share/cauldron/post_start.sh" for call in exec_calls
    )


def test_up_runs_post_start_when_restarting_container(
    fake_podman, monkeypatch, tmp_path
):
    cauldron_dir = tmp_path / ".cauldron"
    cauldron_dir.mkdir()
    (cauldron_dir / "cauldron.toml").write_text('[scripts]\npost_start = "x.sh"\n')
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    container_is_running(fake_podman)

    result = invoke(["up"])

    assert result.exit_code == 0
    assert any(call[0] == "restart" for call in fake_podman.calls())
    exec_calls = [call for call in fake_podman.calls() if call[0] == "exec"]
    assert len(exec_calls) == 1
    assert exec_calls[0][-1] == "/usr/local/share/cauldron/post_start.sh"


def test_up_passes_entrypoint_flag_when_entrypoint_configured(
    fake_podman, monkeypatch, tmp_path
):
    cauldron_dir = tmp_path / ".cauldron"
    cauldron_dir.mkdir()
    (cauldron_dir / "cauldron.toml").write_text(
        '[scripts]\nentrypoint = "entrypoint.sh"\n'
    )
    (cauldron_dir / "entrypoint.sh").write_text('#!/bin/bash\nexec "$@"\n')
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)
    fake_podman.respond(["image", "exists"], returncode=1)
    container_is_running(fake_podman)

    result = invoke(["up"])

    assert result.exit_code == 0
    run_calls = [call for call in fake_podman.calls() if call[0] == "run"]
    assert "sleep" not in run_calls[0]


def test_up_mounts_podman_socket(fake_podman, monkeypatch, tmp_path):
    cauldron_dir = tmp_path / ".cauldron"
    cauldron_dir.mkdir()
    (cauldron_dir / "cauldron.toml").write_text("[container]\npodman_socket = true\n")
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)
    fake_podman.respond(["image", "exists"], returncode=0)
    container_is_running(fake_podman)

    with (
        patch("cauldron.podman_remote.running_in_remote", return_value=False),
        patch(
            "cauldron.podman_remote.ensure_socket",
            return_value="/run/user/1000/podman/podman.sock",
        ),
        patch("cauldron.podman_remote.ensure_client", return_value="/cache/podman"),
    ):
        result = invoke(["up"])

    assert result.exit_code == 0
    run_calls = [call for call in fake_podman.calls() if call[0] == "run"]
    assert "/run/user/1000/podman:/run/podman-host" in run_calls[0]
    assert "CONTAINER_HOST=unix:///run/podman-host/podman.sock" in run_calls[0]


def test_up_nested_reuses_host_socket_and_client(fake_podman, monkeypatch, tmp_path):
    cauldron_dir = tmp_path / ".cauldron"
    cauldron_dir.mkdir()
    (cauldron_dir / "cauldron.toml").write_text("[container]\npodman_socket = true\n")
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)
    fake_podman.respond(["image", "exists"], returncode=0)
    container_is_running(fake_podman)

    with (
        patch("cauldron.podman_remote.running_in_remote", return_value=True),
        patch(
            "cauldron.podman_remote.host_backend_paths",
            return_value=(
                "/run/user/1000/podman/podman.sock",
                "/home/host/.cache/cauldron/podman-remote/5.4.2/podman",
            ),
        ),
        patch("cauldron.podman_remote.host_sibling_mounts", return_value=[]),
        patch("cauldron.podman_remote.host_sibling_env", return_value={}),
    ):
        result = invoke(["up"])

    assert result.exit_code == 0
    run_calls = [call for call in fake_podman.calls() if call[0] == "run"]
    assert "/run/user/1000/podman:/run/podman-host" in run_calls[0]
    assert (
        "/home/host/.cache/cauldron/podman-remote/5.4.2/podman"
        ":/usr/local/bin/cauldron-podman:ro" in run_calls[0]
    )
    assert "CONTAINER_HOST=unix:///run/podman-host/podman.sock" in run_calls[0]


def test_up_nested_reuses_host_mounts_and_env(fake_podman, monkeypatch, tmp_path):
    cauldron_dir = tmp_path / ".cauldron"
    cauldron_dir.mkdir()
    (cauldron_dir / "cauldron.toml").write_text("[container]\npodman_socket = true\n")
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)
    fake_podman.respond(["image", "exists"], returncode=0)
    container_is_running(fake_podman)

    with (
        patch("cauldron.podman_remote.running_in_remote", return_value=True),
        patch(
            "cauldron.podman_remote.host_backend_paths",
            return_value=(
                "/run/user/1000/podman/podman.sock",
                "/home/host/.cache/cauldron/podman-remote/5.4.2/podman",
            ),
        ),
        patch(
            "cauldron.podman_remote.host_sibling_mounts",
            return_value=[
                {
                    "source": "/home/host/.gitconfig",
                    "target": "/home/cauldron/.gitconfig",
                    "options": "ro",
                }
            ],
        ),
        patch(
            "cauldron.podman_remote.host_sibling_env",
            return_value={"SSH_AUTH_SOCK": "/run/user/1000/keyring/ssh"},
        ),
    ):
        result = invoke(["up"])

    assert result.exit_code == 0
    run_calls = [call for call in fake_podman.calls() if call[0] == "run"]
    assert "/home/host/.gitconfig:/home/cauldron/.gitconfig:ro" in run_calls[0]
    assert "SSH_AUTH_SOCK=/run/user/1000/keyring/ssh" in run_calls[0]


def test_up_reports_missing_podman_socket(fake_podman, monkeypatch, tmp_path):
    cauldron_dir = tmp_path / ".cauldron"
    cauldron_dir.mkdir()
    (cauldron_dir / "cauldron.toml").write_text("[container]\npodman_socket = true\n")
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)
    fake_podman.respond(["image", "exists"], returncode=0)

    with (
        patch("cauldron.podman_remote.running_in_remote", return_value=False),
        patch(
            "cauldron.podman_remote.ensure_socket",
            side_effect=podman_remote.RemoteError("no socket"),
        ),
    ):
        result = invoke(["up"])

    assert result.exit_code != 0
    assert "no socket" in result.output
    assert not any(call[0] == "run" for call in fake_podman.calls())


def test_up_fails_when_post_build_image_build_fails(fake_podman, monkeypatch, tmp_path):
    cauldron_dir = tmp_path / ".cauldron"
    cauldron_dir.mkdir()
    (cauldron_dir / "cauldron.toml").write_text(
        '[scripts]\npost_build = "post_build.sh"\n'
    )
    (cauldron_dir / "post_build.sh").write_text("#!/bin/bash\necho build\n")
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)
    fake_podman.respond(["image", "exists"], returncode=1)
    fake_podman.respond(["build"], returncode=1)

    result = invoke(["up"])

    assert result.exit_code != 0
    assert "Failed to build project image" in result.output


def test_code_fails_when_code_cli_missing(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    container_is_running(fake_podman)

    with patch("cauldron.vscode.code_available", return_value=False):
        result = invoke(["code"])

    assert result.exit_code != 0
    assert "VSCode CLI" in result.output


def test_code_opens_project_in_vscode(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    container_is_running(fake_podman)

    with (
        patch("cauldron.vscode.code_available", return_value=True),
        patch("cauldron.ssh.ensure_keypair"),
        patch("cauldron.ssh.ensure_container_ssh"),
        patch("cauldron.ssh.ensure_ssh_config"),
        patch("cauldron.vscode.open_in_code", return_value=0) as mock_open,
    ):
        result = invoke(["code"])

    assert result.exit_code == 0
    mock_open.assert_called_once()
    args, _kwargs = mock_open.call_args
    assert "cauldron-" in args[0]


def test_code_propagates_nonzero_exit_code(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    container_is_running(fake_podman)

    with (
        patch("cauldron.vscode.code_available", return_value=True),
        patch("cauldron.ssh.ensure_keypair"),
        patch("cauldron.ssh.ensure_container_ssh"),
        patch("cauldron.ssh.ensure_ssh_config"),
        patch("cauldron.vscode.open_in_code", return_value=1),
    ):
        result = invoke(["code"])

    assert result.exit_code == 1


def test_code_auto_starts_container_when_not_exists(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman, returncode=1)
    fake_podman.respond(["image", "exists"], returncode=0)
    container_is_running(fake_podman)

    with (
        patch("cauldron.vscode.code_available", return_value=True),
        patch("cauldron.ssh.ensure_keypair"),
        patch("cauldron.ssh.ensure_container_ssh"),
        patch("cauldron.ssh.ensure_ssh_config"),
        patch("cauldron.vscode.open_in_code", return_value=0) as mock_open,
    ):
        result = invoke(["code"])

    assert result.exit_code == 0
    assert any(call[0] == "run" for call in fake_podman.calls())
    mock_open.assert_called_once()


def test_code_fails_when_ssh_setup_errors(fake_podman, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    containers_exist(fake_podman)
    container_is_running(fake_podman)

    with (
        patch("cauldron.vscode.code_available", return_value=True),
        patch("cauldron.ssh.ensure_keypair"),
        patch(
            "cauldron.ssh.ensure_container_ssh",
            side_effect=ssh.SSHError("test error"),
        ),
        patch("cauldron.ssh.ensure_ssh_config"),
    ):
        result = invoke(["code"])

    assert result.exit_code != 0
    assert "test error" in result.output


def test_init_creates_cauldron_directory_and_templates():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "Created .cauldron/Dockerfile" in result.output
        assert "Created .cauldron/cauldron.toml" in result.output
        assert "Created .cauldron/post_build.sh" in result.output

        import pathlib

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
        assert "known_hosts" in content
        assert "# podman_socket = true" in content
        assert ".gitconfig" in content
        assert "SSH_AUTH_SOCK" in content

        script = pathlib.Path(".cauldron/post_build.sh")
        assert script.read_text() == (
            "#!/bin/bash\n"
            "# Add commands required to prepare the project image here.\n"
            "set -euxo pipefail\n"
        )
        assert script.stat().st_mode & 0o111


def test_init_keeps_existing_files():
    runner = CliRunner()
    with runner.isolated_filesystem():
        import pathlib

        pathlib.Path(".cauldron").mkdir()
        pathlib.Path(".cauldron/Dockerfile").write_text("FROM custom\n")
        pathlib.Path(".cauldron/cauldron.toml").write_text('[env]\nFOO = "bar"\n')
        pathlib.Path(".cauldron/post_build.sh").write_text("custom\n")

        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "Keeping existing .cauldron/Dockerfile" in result.output
        assert "Keeping existing .cauldron/cauldron.toml" in result.output
        assert "Keeping existing .cauldron/post_build.sh" in result.output
        assert pathlib.Path(".cauldron/Dockerfile").read_text() == "FROM custom\n"
        assert (
            pathlib.Path(".cauldron/cauldron.toml").read_text()
            == '[env]\nFOO = "bar"\n'
        )
        assert pathlib.Path(".cauldron/post_build.sh").read_text() == "custom\n"
