import subprocess
from unittest.mock import MagicMock, patch

import pytest

from cauldron import podman


@pytest.fixture(autouse=True)
def reset_verbose():
    podman.set_verbose(False)
    yield
    podman.set_verbose(False)


def test_set_verbose_changes_flag():
    podman.set_verbose(True)
    assert podman._verbose is True
    podman.set_verbose(False)
    assert podman._verbose is False


def test_run_uses_subprocess_run_when_not_verbose():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        podman._run(["version"])
        mock_run.assert_called_once_with(
            ["podman", "version"], capture_output=True, text=True
        )


def test_run_uses_popen_when_verbose():
    podman.set_verbose(True)
    mock_process = MagicMock()
    mock_process.wait.return_value = 0
    mock_process.stdout = MagicMock()
    mock_process.stdout.readline = MagicMock(return_value="")
    mock_process.stderr = MagicMock()
    mock_process.stderr.readline = MagicMock(return_value="")

    with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
        result = podman._run(["version"])
        mock_popen.assert_called_once_with(
            ["podman", "version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert result.returncode == 0


def test_run_returns_completed_process_on_success():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        result = podman._run(["version"])
        assert result.returncode == 0
        mock_run.assert_called_once_with(
            ["podman", "version"], capture_output=True, text=True
        )


def test_run_returns_nonzero_when_podman_not_found():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        result = podman._run(["version"])
        assert result.returncode == 1


def test_version_returns_true_when_podman_works():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert podman.version() is True
        mock_run.assert_called_once_with(["version"])


def test_version_returns_false_when_podman_fails():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 1
        assert podman.version() is False


def test_image_id_returns_id_on_success():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "sha256:abc123\n"
        assert podman.image_id("debian:trixie-slim") == "sha256:abc123"
        mock_run.assert_called_once_with(
            ["image", "inspect", "debian:trixie-slim", "-f", "{{.Id}}"]
        )


def test_image_id_returns_none_on_failure():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 1
        assert podman.image_id("debian:trixie-slim") is None


def test_ensure_base_image_returns_true_if_default_image_exists():
    with patch("cauldron.podman.image_exists", return_value=True) as mock_exists:
        assert podman.ensure_base_image() is True
        mock_exists.assert_called_once_with(podman.BASE_IMAGE)


def test_ensure_base_image_returns_true_if_configured_image_exists():
    with patch("cauldron.podman.image_exists", return_value=True) as mock_exists:
        assert podman.ensure_base_image("astral/uv:python3.14-trixie") is True
        mock_exists.assert_called_once_with("astral/uv:python3.14-trixie")


def test_ensure_base_image_pulls_default_when_missing():
    with patch("cauldron.podman.image_exists", return_value=False):
        with patch("cauldron.podman.pull", return_value=True) as mock_pull:
            assert podman.ensure_base_image() is True
            mock_pull.assert_called_once_with(podman.BASE_IMAGE)


def test_ensure_base_image_pulls_configured_when_missing():
    with patch("cauldron.podman.image_exists", return_value=False):
        with patch("cauldron.podman.pull", return_value=True) as mock_pull:
            assert podman.ensure_base_image("astral/uv:python3.14-trixie") is True
            mock_pull.assert_called_once_with("astral/uv:python3.14-trixie")


def test_ensure_base_image_fails_when_pull_fails():
    with patch("cauldron.podman.image_exists", return_value=False):
        with patch("cauldron.podman.pull", return_value=False):
            assert podman.ensure_base_image() is False


def test_run_test_container_uses_default_base_image():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert podman.run_test_container() is True
        mock_run.assert_called_once_with(
            ["run", "--rm", podman.BASE_IMAGE, "echo", "cauldron-check-ok"]
        )


def test_run_test_container_uses_configured_base_image():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert podman.run_test_container("astral/uv:python3.14-trixie") is True
        mock_run.assert_called_once_with(
            ["run", "--rm", "astral/uv:python3.14-trixie", "echo", "cauldron-check-ok"]
        )


def test_build_image_runs_podman_build():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert (
            podman.build_image("cauldron-foo:latest", "/path/Dockerfile", "/context")
            is True
        )
        mock_run.assert_called_once_with(
            ["build", "-t", "cauldron-foo:latest", "-f", "/path/Dockerfile", "/context"]
        )


def test_build_image_passes_build_args():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert (
            podman.build_image(
                "cauldron-foo:latest",
                "/path/Dockerfile",
                "/context",
                build_args={"CAULDRON_BASE": "astral/uv:python3.14-trixie"},
            )
            is True
        )
        mock_run.assert_called_once_with(
            [
                "build",
                "-t",
                "cauldron-foo:latest",
                "-f",
                "/path/Dockerfile",
                "--build-arg",
                "CAULDRON_BASE=astral/uv:python3.14-trixie",
                "/context",
            ]
        )


def test_build_project_image_generates_dockerfile(tmp_path):
    captured = {"args": None}

    def capture_build(_tag, dockerfile, _context, build_args=None):
        captured["content"] = dockerfile.read_text()
        captured["args"] = build_args
        return True

    with patch("cauldron.podman.build_image", side_effect=capture_build):
        assert (
            podman.build_project_image("cauldron-proj:latest", "1000", "1000") is True
        )
        content = captured["content"]
        assert "ARG CAULDRON_BASE" in content
        assert "FROM ${CAULDRON_BASE}" in content
        assert "groupadd -g 1000 -o cauldron" in content
        assert "useradd -m -u 1000 -g 1000 -o cauldron" in content
        assert "HOME=/home/cauldron" in content
        assert captured["args"] == {"CAULDRON_BASE": podman.BASE_IMAGE}


def test_build_project_image_uses_configured_base():
    captured = {"args": None}

    def capture_build(_tag, dockerfile, _context, build_args=None):
        captured["content"] = dockerfile.read_text()
        captured["args"] = build_args
        return True

    with patch("cauldron.podman.build_image", side_effect=capture_build):
        assert (
            podman.build_project_image(
                "cauldron-proj:latest",
                "1000",
                "1000",
                "cauldron-proj-intermediate:latest",
            )
            is True
        )
        assert "FROM ${CAULDRON_BASE}" in captured["content"]
        assert captured["args"] == {
            "CAULDRON_BASE": "cauldron-proj-intermediate:latest"
        }


def test_container_exists_returns_true_when_container_present():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert podman.container_exists("cauldron-foo") is True
        mock_run.assert_called_once_with(["container", "exists", "cauldron-foo"])


def test_container_running_inspects_state():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "true\n"
        assert podman.container_running("cauldron-foo") is True


def test_container_running_returns_false_when_not_running():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "false\n"
        assert podman.container_running("cauldron-foo") is False


def test_container_shell_returns_shell_from_container():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "/bin/zsh\n"
        assert podman.container_shell("cauldron-foo") == "/bin/zsh"
        mock_run.assert_called_once_with(["exec", "cauldron-foo", "printenv", "SHELL"])


def test_container_shell_returns_fallback_when_unset():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 1
        assert podman.container_shell("cauldron-foo") == "bash"


def test_container_shell_returns_fallback_when_empty():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "\n"
        assert podman.container_shell("cauldron-foo") == "bash"


def test_container_shell_accepts_custom_fallback():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 1
        assert podman.container_shell("cauldron-foo", fallback="sh") == "sh"


def test_image_env_parses_inspect_output():
    json_output = '["PATH=/usr/bin:/bin","FOO=bar"]'
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = json_output
        assert podman.image_env("cauldron-foo:latest") == {
            "PATH": "/usr/bin:/bin",
            "FOO": "bar",
        }
        mock_run.assert_called_once_with(
            [
                "image",
                "inspect",
                "cauldron-foo:latest",
                "--format",
                "{{json .Config.Env}}",
            ]
        )


def test_image_env_returns_empty_on_failure():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 1
        assert podman.image_env("cauldron-foo:latest") == {}


def test_run_container_passes_env_vars():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        podman.run_container(
            name="cauldron-foo",
            image="cauldron-foo:latest",
            workdir="/home/deck/projects/foo",
            project_dir="/home/deck/projects/foo",
            uid="1000",
            gid="1000",
            env={"EDITOR": "vim", "FOO": "bar"},
        )
        args = mock_run.call_args[0][0]
        assert "EDITOR=vim" in args
        assert "FOO=bar" in args


def test_run_container_expands_path_placeholder():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0

        def inspect_side_effect(args):
            result = MagicMock()
            result.returncode = 0
            result.stdout = '["PATH=/usr/bin:/bin"]'
            return result

        mock_run.side_effect = inspect_side_effect
        podman.run_container(
            name="cauldron-foo",
            image="cauldron-foo:latest",
            workdir="/home/deck/projects/foo",
            project_dir="/home/deck/projects/foo",
            uid="1000",
            gid="1000",
            env={"PATH": "/extra/bin:${PATH}"},
        )
        args = mock_run.call_args[0][0]
        assert "PATH=/extra/bin:/usr/bin:/bin" in args


def test_run_container_mounts_defaults():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert (
            podman.run_container(
                name="cauldron-foo",
                image="cauldron-foo:latest",
                workdir="/home/deck/projects/foo",
                project_dir="/home/deck/projects/foo",
                uid="1000",
                gid="1000",
            )
            is True
        )
        args = mock_run.call_args[0][0]
        assert "run" in args
        assert "-d" in args
        assert "--name" in args
        assert "cauldron-foo" in args
        assert "--userns" in args
        assert "keep-id" in args
        assert "--user" in args
        assert "1000:1000" in args
        assert "/home/deck/projects/foo:/home/deck/projects/foo:rw,Z" in args
        assert args[-2:] == ["sleep", "infinity"]


def test_run_container_adds_project_label():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        podman.run_container(
            name="cauldron-foo",
            image="cauldron-foo:latest",
            workdir="/home/deck/projects/foo",
            project_dir="/home/deck/projects/foo",
            uid="1000",
            gid="1000",
        )
        args = mock_run.call_args[0][0]
        assert "--label" in args
        assert "cauldron.project_dir=/home/deck/projects/foo" in args


def test_run_container_mounts_extra_volumes():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        podman.run_container(
            name="cauldron-foo",
            image="cauldron-foo:latest",
            workdir="/home/deck/projects/foo",
            project_dir="/home/deck/projects/foo",
            uid="1000",
            gid="1000",
            mounts=[
                {"source": "/host/extra", "target": "/extra", "options": "ro"},
                {"source": "/host/cache", "target": "/cache"},
            ],
        )
        args = mock_run.call_args[0][0]
        assert "/host/extra:/extra:ro" in args
        assert "/host/cache:/cache" in args


def test_run_container_publishes_ports():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        podman.run_container(
            name="cauldron-foo",
            image="cauldron-foo:latest",
            workdir="/home/deck/projects/foo",
            project_dir="/home/deck/projects/foo",
            uid="1000",
            gid="1000",
            ports=["8080:8080", "127.0.0.1:3000:3000"],
        )
        args = mock_run.call_args[0][0]
        assert "-p" in args
        assert "8080:8080" in args
        assert "127.0.0.1:3000:3000" in args


def test_run_container_warns_when_selinux_enabled_and_no_label():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        with patch("cauldron.podman._selinux_enabled", return_value=True):
            with pytest.warns(UserWarning, match="SELinux relabel option"):
                podman.run_container(
                    name="cauldron-foo",
                    image="cauldron-foo:latest",
                    workdir="/home/deck/projects/foo",
                    project_dir="/home/deck/projects/foo",
                    uid="1000",
                    gid="1000",
                    mounts=[
                        {"source": "/host/extra", "target": "/extra", "options": "ro"},
                    ],
                )


def test_run_container_does_not_warn_when_selinux_label_present():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        with patch("cauldron.podman._selinux_enabled", return_value=True):
            with patch("warnings.warn") as mock_warn:
                podman.run_container(
                    name="cauldron-foo",
                    image="cauldron-foo:latest",
                    workdir="/home/deck/projects/foo",
                    project_dir="/home/deck/projects/foo",
                    uid="1000",
                    gid="1000",
                    mounts=[
                        {
                            "source": "/host/extra",
                            "target": "/extra",
                            "options": "ro,Z",
                        },
                    ],
                )
                mock_warn.assert_not_called()


def test_run_container_does_not_warn_when_selinux_disabled():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        with patch("cauldron.podman._selinux_enabled", return_value=False):
            with patch("warnings.warn") as mock_warn:
                podman.run_container(
                    name="cauldron-foo",
                    image="cauldron-foo:latest",
                    workdir="/home/deck/projects/foo",
                    project_dir="/home/deck/projects/foo",
                    uid="1000",
                    gid="1000",
                    mounts=[
                        {"source": "/host/extra", "target": "/extra", "options": "ro"},
                    ],
                )
                mock_warn.assert_not_called()


def test_start_container_runs_podman_start():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert podman.start_container("cauldron-foo") is True
        mock_run.assert_called_once_with(["start", "cauldron-foo"])


def test_restart_container_runs_podman_restart():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert podman.restart_container("cauldron-foo") is True
        mock_run.assert_called_once_with(["restart", "cauldron-foo"])


def test_container_id_returns_id():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "abc123\n"
        assert podman.container_id("cauldron-foo") == "abc123"
        mock_run.assert_called_once_with(
            ["container", "inspect", "-f", "{{.Id}}", "cauldron-foo"]
        )


def test_container_id_returns_none_on_failure():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 1
        assert podman.container_id("cauldron-foo") is None


def test_stop_container_runs_podman_stop():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert podman.stop_container("cauldron-foo") is True
        mock_run.assert_called_once_with(["stop", "cauldron-foo"])


def test_remove_container_runs_podman_rm():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert podman.remove_container("cauldron-foo") is True
        mock_run.assert_called_once_with(["rm", "cauldron-foo"])


def test_remove_container_forces_when_requested():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert podman.remove_container("cauldron-foo", force=True) is True
        mock_run.assert_called_once_with(["rm", "-f", "cauldron-foo"])


def test_exec_in_container_runs_command():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        assert podman.exec_in_container("cauldron-foo", "ls", ["-la"]) == 0
        mock_run.assert_called_once_with(
            ["podman", "exec", "cauldron-foo", "ls", "-la"]
        )


def test_exec_in_container_uses_interactive_and_tty():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        podman.exec_in_container("cauldron-foo", "bash", interactive=True, tty=True)
        mock_run.assert_called_once_with(
            ["podman", "exec", "-i", "-t", "cauldron-foo", "bash"]
        )


def test_exec_in_container_returns_podman_not_found():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        assert podman.exec_in_container("cauldron-foo", "ls") == 1


def test_list_containers_parses_podman_ps_json():
    json_output = """[
        {
            "Names": ["cauldron-foo"],
            "Image": "cauldron-foo:latest",
            "State": "running",
            "Labels": {"cauldron.project_dir": "/home/deck/projects/foo"}
        }
    ]"""
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = json_output
        containers = podman.list_containers()
        assert len(containers) == 1
        assert containers[0]["name"] == "cauldron-foo"
        assert containers[0]["image"] == "cauldron-foo:latest"
        assert containers[0]["status"] == "running"
        assert containers[0]["project_dir"] == "/home/deck/projects/foo"


def test_list_containers_returns_empty_on_failure():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 1
        assert podman.list_containers() == []
