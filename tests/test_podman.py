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
        assert podman.image_id("cauldron-base:latest") == "sha256:abc123"
        mock_run.assert_called_once_with(
            ["image", "inspect", "cauldron-base:latest", "-f", "{{.Id}}"]
        )


def test_image_id_returns_none_on_failure():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 1
        assert podman.image_id("cauldron-base:latest") is None


def test_ensure_base_image_returns_true_if_image_exists():
    with patch("cauldron.podman.image_exists", return_value=True) as mock_exists:
        assert podman.ensure_base_image() is True
        mock_exists.assert_called_once_with("cauldron-base:latest")


def test_ensure_base_image_pulls_and_tags_when_missing():
    with patch("cauldron.podman.image_exists", return_value=False):
        with patch("cauldron.podman.pull", return_value=True) as mock_pull:
            with patch("cauldron.podman.tag", return_value=True) as mock_tag:
                assert podman.ensure_base_image() is True
                mock_pull.assert_called_once_with(podman.BASE_IMAGE)
                mock_tag.assert_called_once_with(
                    podman.BASE_IMAGE, podman.LOCAL_BASE_TAG
                )


def test_ensure_base_image_uses_configured_base_image():
    with patch("cauldron.podman.image_exists", return_value=False):
        with patch("cauldron.podman.pull", return_value=True) as mock_pull:
            with patch("cauldron.podman.tag", return_value=True) as mock_tag:
                assert podman.ensure_base_image("astral/uv:python3.14-trixie") is True
                mock_pull.assert_called_once_with("astral/uv:python3.14-trixie")
                mock_tag.assert_called_once_with(
                    "astral/uv:python3.14-trixie", podman.LOCAL_BASE_TAG
                )


def test_ensure_base_image_skips_pull_when_local_tag_matches_configured():
    with patch("cauldron.podman.image_exists", return_value=True):
        with patch(
            "cauldron.podman.image_id", return_value="sha256:abc123"
        ) as mock_image_id:
            with patch("cauldron.podman.pull") as mock_pull:
                assert podman.ensure_base_image("astral/uv:python3.14-trixie") is True
                mock_image_id.assert_any_call("cauldron-base:latest")
                mock_image_id.assert_any_call("astral/uv:python3.14-trixie")
                mock_pull.assert_not_called()


def test_ensure_base_image_fails_when_pull_fails():
    with patch("cauldron.podman.image_exists", return_value=False):
        with patch("cauldron.podman.pull", return_value=False):
            assert podman.ensure_base_image() is False


def test_run_test_container_returns_true_on_success():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert podman.run_test_container() is True
        mock_run.assert_called_once_with(
            ["run", "--rm", "cauldron-base:latest", "echo", "cauldron-check-ok"]
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


def test_build_project_image_generates_dockerfile(tmp_path):
    captured = {}

    def capture_build(_tag, dockerfile, _context):
        captured["content"] = dockerfile.read_text()
        return True

    with patch("cauldron.podman.build_image", side_effect=capture_build):
        assert (
            podman.build_project_image("cauldron-proj:latest", "1000", "1000") is True
        )
        content = captured["content"]
        assert "FROM cauldron-base:latest" in content
        assert "groupadd -g 1000 -o cauldron" in content
        assert "useradd -m -u 1000 -g 1000 -o cauldron" in content
        assert "HOME=/home/cauldron" in content


def test_build_project_image_uses_intermediate_base():
    captured = {}

    def capture_build(_tag, dockerfile, _context):
        captured["content"] = dockerfile.read_text()
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
        assert "FROM cauldron-proj-intermediate:latest" in captured["content"]


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


def test_run_container_mounts_gitconfig_and_ssh_agent():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        podman.run_container(
            name="cauldron-foo",
            image="cauldron-foo:latest",
            workdir="/home/deck/projects/foo",
            project_dir="/home/deck/projects/foo",
            uid="1000",
            gid="1000",
            gitconfig="/home/deck/.gitconfig",
            ssh_auth_sock="/run/user/1000/keyring/ssh",
        )
        args = mock_run.call_args[0][0]
        assert "/home/deck/.gitconfig:/home/cauldron/.gitconfig:ro,Z" in args
        assert "/run/user/1000/keyring/ssh:/run/user/1000/keyring/ssh:ro" in args
        assert "SSH_AUTH_SOCK=/run/user/1000/keyring/ssh" in args


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
