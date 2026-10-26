"""Contract tests for the Podman wrapper.

Argument-level behaviour is asserted through the recording fake (see
``conftest.py``); tests that need a live Podman service live in
``test_podman_integration.py``. Pure helpers and Dockerfile generation are
tested directly.
"""

from unittest.mock import patch

import pytest

from cauldron import podman


def test_set_verbose_changes_flag():
    podman.set_verbose(True)
    assert podman._verbose is True
    podman.set_verbose(False)
    assert podman._verbose is False


def test_run_returns_completed_process(fake_podman):
    result = podman._run(["version"])
    assert result.returncode == 0
    assert fake_podman.calls() == [["version"]]


def test_run_streams_output_when_verbose(fake_podman):
    fake_podman.respond(["version"], stdout="fake-version\n")
    podman.set_verbose(True)
    result = podman._run(["version"])
    assert result.returncode == 0
    assert result.stdout == "fake-version\n"


def test_run_returns_nonzero_when_podman_not_found(monkeypatch):
    monkeypatch.setenv("CAULDRON_PODMAN_PATH", "/nonexistent/podman")
    result = podman._run(["version"])
    assert result.returncode == 1


def test_version_returns_false_when_podman_fails(fake_podman):
    fake_podman.respond(["version"], returncode=1)
    assert podman.version() is False


def test_image_id_inspects_id_format(fake_podman):
    podman.image_id("debian:trixie-slim")
    assert fake_podman.calls() == [
        ["image", "inspect", "debian:trixie-slim", "-f", "{{.Id}}"]
    ]


def test_ensure_base_image_returns_true_when_image_exists(fake_podman):
    assert podman.ensure_base_image() is True
    assert fake_podman.calls() == [["image", "exists", podman.BASE_IMAGE]]


def test_ensure_base_image_uses_configured_image(fake_podman):
    assert podman.ensure_base_image("astral/uv:python3.14-trixie") is True
    assert fake_podman.calls() == [["image", "exists", "astral/uv:python3.14-trixie"]]


def test_ensure_base_image_pulls_when_missing(fake_podman):
    fake_podman.respond(["image", "exists"], returncode=1)
    fake_podman.respond(["pull"], returncode=0)
    assert podman.ensure_base_image("astral/uv:python3.14-trixie") is True
    assert fake_podman.calls() == [
        ["image", "exists", "astral/uv:python3.14-trixie"],
        ["pull", "astral/uv:python3.14-trixie"],
    ]


def test_ensure_base_image_fails_when_pull_fails(fake_podman):
    fake_podman.respond(["image", "exists"], returncode=1)
    fake_podman.respond(["pull"], returncode=1)
    assert podman.ensure_base_image() is False


def test_run_test_container_uses_default_base_image(fake_podman):
    assert podman.run_test_container() is True
    assert fake_podman.calls() == [
        ["run", "--rm", podman.BASE_IMAGE, "echo", "cauldron-check-ok"]
    ]


def test_run_test_container_uses_configured_base_image(fake_podman):
    assert podman.run_test_container("astral/uv:python3.14-trixie") is True
    assert fake_podman.calls() == [
        ["run", "--rm", "astral/uv:python3.14-trixie", "echo", "cauldron-check-ok"]
    ]


def test_build_image_runs_podman_build(fake_podman):
    result = podman.build_image("cauldron-foo:latest", "/path/Dockerfile", "/context")
    assert result.returncode == 0
    assert fake_podman.calls() == [
        ["build", "-t", "cauldron-foo:latest", "-f", "/path/Dockerfile", "/context"]
    ]


def test_build_image_passes_build_args(fake_podman):
    result = podman.build_image(
        "cauldron-foo:latest",
        "/path/Dockerfile",
        "/context",
        build_args={"CAULDRON_BASE": "astral/uv:python3.14-trixie"},
    )
    assert result.returncode == 0
    assert fake_podman.calls() == [
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
    ]


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


def test_container_exists_checks_named_container(fake_podman):
    podman.container_exists("cauldron-foo")
    assert fake_podman.calls() == [["container", "exists", "cauldron-foo"]]


def test_container_running_inspects_state_flag(fake_podman):
    podman.container_running("cauldron-foo")
    assert fake_podman.calls() == [
        ["container", "inspect", "-f", "{{.State.Running}}", "cauldron-foo"]
    ]


def test_container_shell_probes_printenv_shell(fake_podman):
    podman.container_shell("cauldron-foo")
    assert fake_podman.calls() == [["exec", "cauldron-foo", "printenv", "SHELL"]]


def test_container_shell_returns_fallback_when_unset(fake_podman):
    fake_podman.respond(["exec"], returncode=1)
    assert podman.container_shell("cauldron-foo") == "bash"


def test_container_shell_accepts_custom_fallback(fake_podman):
    fake_podman.respond(["exec"], returncode=1)
    assert podman.container_shell("cauldron-foo", fallback="sh") == "sh"


def test_image_env_parses_and_filters_inspect_output(fake_podman):
    # Real logic: parse the JSON array and keep only KEY=VALUE entries.
    fake_podman.respond(
        ["image", "inspect"],
        stdout='["PATH=/usr/bin:/bin","FOO=bar","BARE_FLAG"]',
    )
    assert podman.image_env("cauldron-foo:latest") == {
        "PATH": "/usr/bin:/bin",
        "FOO": "bar",
    }
    assert fake_podman.calls() == [
        [
            "image",
            "inspect",
            "cauldron-foo:latest",
            "--format",
            "{{json .Config.Env}}",
        ]
    ]


def test_run_container_passes_env_vars(fake_podman):
    assert (
        podman.run_container(
            name="cauldron-foo",
            image="cauldron-foo:latest",
            workdir="/home/deck/projects/foo",
            project_dir="/home/deck/projects/foo",
            uid="1000",
            gid="1000",
            env={"EDITOR": "vim", "FOO": "bar"},
        )
        is True
    )
    args = fake_podman.calls()[0]
    assert "EDITOR=vim" in args
    assert "FOO=bar" in args


def test_run_container_expands_path_placeholder(fake_podman):
    fake_podman.respond(["image", "inspect"], stdout='["PATH=/usr/bin:/bin"]')
    podman.run_container(
        name="cauldron-foo",
        image="cauldron-foo:latest",
        workdir="/home/deck/projects/foo",
        project_dir="/home/deck/projects/foo",
        uid="1000",
        gid="1000",
        env={"PATH": "/extra/bin:${PATH}"},
    )
    args = fake_podman.calls()[-1]
    assert "PATH=/extra/bin:/usr/bin:/bin" in args


def test_run_container_mounts_defaults(fake_podman):
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
    args = fake_podman.calls()[0]
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


def test_build_project_image_owns_config_directory():
    dockerfile_content = []

    def capture_build(_tag, dockerfile, _context, build_args=None):
        dockerfile_content.append(dockerfile.read_text())
        return True

    with patch("cauldron.podman.build_image", side_effect=capture_build):
        assert podman.build_project_image(
            tag="cauldron-foo:latest",
            uid="1000",
            gid="1000",
            project_dir="/home/deck/projects/foo",
        )

    assert "/home/cauldron/.config" in dockerfile_content[0]
    assert (
        "chown 1000:1000 /home/cauldron/.ssh /home/cauldron/.config"
        in dockerfile_content[0]
    )


def test_run_container_adds_project_label(fake_podman):
    podman.run_container(
        name="cauldron-foo",
        image="cauldron-foo:latest",
        workdir="/home/deck/projects/foo",
        project_dir="/home/deck/projects/foo",
        uid="1000",
        gid="1000",
    )
    args = fake_podman.calls()[0]
    assert "--label" in args
    assert "cauldron.project_dir=/home/deck/projects/foo" in args


def test_run_container_mounts_extra_volumes(fake_podman):
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
    args = fake_podman.calls()[0]
    assert "/host/extra:/extra:ro" in args
    assert "/host/cache:/cache" in args


def test_run_container_publishes_ports(fake_podman):
    podman.run_container(
        name="cauldron-foo",
        image="cauldron-foo:latest",
        workdir="/home/deck/projects/foo",
        project_dir="/home/deck/projects/foo",
        uid="1000",
        gid="1000",
        ports=["8080:8080", "127.0.0.1:3000:3000"],
    )
    args = fake_podman.calls()[0]
    assert "-p" in args
    assert "8080:8080" in args
    assert "127.0.0.1:3000:3000" in args


def test_run_container_adds_known_hosts(fake_podman):
    podman.run_container(
        name="cauldron-foo",
        image="cauldron-foo:latest",
        workdir="/home/deck/projects/foo",
        project_dir="/home/deck/projects/foo",
        uid="1000",
        gid="1000",
        known_hosts=["my-service.local:127.0.0.1", "registry.internal:10.0.0.5"],
    )
    args = fake_podman.calls()[0]
    assert "--add-host" in args
    assert "my-service.local:127.0.0.1" in args
    assert "registry.internal:10.0.0.5" in args


def test_podman_bin_defaults_to_podman(monkeypatch):
    monkeypatch.delenv("CAULDRON_PODMAN_PATH", raising=False)
    assert podman._podman_bin() == "podman"


def test_podman_bin_honours_configured_path(monkeypatch):
    monkeypatch.setenv("CAULDRON_PODMAN_PATH", "/usr/local/bin/cauldron-podman")
    assert podman._podman_bin() == "/usr/local/bin/cauldron-podman"


def test_run_container_mounts_podman_socket_and_client(fake_podman):
    podman.run_container(
        name="cauldron-foo",
        image="cauldron-foo:latest",
        workdir="/home/deck/projects/foo",
        project_dir="/home/deck/projects/foo",
        uid="1000",
        gid="1000",
        podman_socket="/run/user/1000/podman/podman.sock",
        podman_client="/home/deck/.cache/cauldron/podman-remote/5.4.2/podman",
    )
    args = fake_podman.calls()[0]
    assert "/run/user/1000/podman:/run/podman-host" in args
    assert (
        "/home/deck/.cache/cauldron/podman-remote/5.4.2/podman:"
        "/usr/local/bin/cauldron-podman:ro" in args
    )
    assert "CONTAINER_HOST=unix:///run/podman-host/podman.sock" in args
    assert "CAULDRON_PODMAN_PATH=/usr/local/bin/cauldron-podman" in args


def test_run_container_omits_podman_socket_by_default(fake_podman):
    podman.run_container(
        name="cauldron-foo",
        image="cauldron-foo:latest",
        workdir="/home/deck/projects/foo",
        project_dir="/home/deck/projects/foo",
        uid="1000",
        gid="1000",
    )
    args = fake_podman.calls()[0]
    assert "/run/podman-host" not in args
    assert not any("CONTAINER_HOST=" in arg for arg in args)


def test_run_container_warns_when_selinux_enabled_and_no_label(fake_podman):
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


def test_run_container_does_not_warn_when_selinux_label_present(fake_podman):
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


def test_run_container_does_not_warn_when_selinux_disabled(fake_podman):
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


def test_start_container_runs_podman_start(fake_podman):
    assert podman.start_container("cauldron-foo") is True
    assert fake_podman.calls() == [["start", "cauldron-foo"]]


def test_restart_container_runs_podman_restart(fake_podman):
    assert podman.restart_container("cauldron-foo") is True
    assert fake_podman.calls() == [["restart", "cauldron-foo"]]


def test_container_id_inspects_id_format(fake_podman):
    podman.container_id("cauldron-foo")
    assert fake_podman.calls() == [
        ["container", "inspect", "-f", "{{.Id}}", "cauldron-foo"]
    ]


def test_container_mounts_parses_mount_list(fake_podman):
    fake_podman.respond(
        ["container", "inspect", "-f", "{{json .Mounts}}", "cauldron-foo"],
        stdout='[{"Source": "/a", "Destination": "/b"}]\n',
    )
    assert podman.container_mounts("cauldron-foo") == [
        {"Source": "/a", "Destination": "/b"}
    ]


def test_container_mounts_empty_on_failure(fake_podman):
    fake_podman.respond(
        ["container", "inspect", "-f", "{{json .Mounts}}", "cauldron-foo"],
        returncode=1,
    )
    assert podman.container_mounts("cauldron-foo") == []


def test_container_env_of_parses_env_list(fake_podman):
    fake_podman.respond(
        ["container", "inspect", "-f", "{{json .Config.Env}}", "cauldron-foo"],
        stdout='["PATH=/a:/b", "HOME=/home/cauldron"]\n',
    )
    assert podman.container_env_of("cauldron-foo") == {
        "PATH": "/a:/b",
        "HOME": "/home/cauldron",
    }


def test_container_env_of_empty_on_failure(fake_podman):
    fake_podman.respond(
        ["container", "inspect", "-f", "{{json .Config.Env}}", "cauldron-foo"],
        returncode=1,
    )
    assert podman.container_env_of("cauldron-foo") == {}


def test_stop_container_runs_podman_stop(fake_podman):
    assert podman.stop_container("cauldron-foo") is True
    assert fake_podman.calls() == [["stop", "cauldron-foo"]]


def test_remove_container_runs_podman_rm(fake_podman):
    assert podman.remove_container("cauldron-foo") is True
    assert fake_podman.calls() == [["rm", "cauldron-foo"]]


def test_remove_container_forces_when_requested(fake_podman):
    assert podman.remove_container("cauldron-foo", force=True) is True
    assert fake_podman.calls() == [["rm", "-f", "cauldron-foo"]]


def test_exec_in_container_runs_command(fake_podman):
    assert podman.exec_in_container("cauldron-foo", "ls", ["-la"]) == 0
    assert fake_podman.calls() == [["exec", "cauldron-foo", "ls", "-la"]]


def test_exec_in_container_uses_interactive_and_tty(fake_podman):
    podman.exec_in_container("cauldron-foo", "bash", interactive=True, tty=True)
    assert fake_podman.calls() == [["exec", "-i", "-t", "cauldron-foo", "bash"]]


def test_exec_in_container_returns_podman_not_found(monkeypatch):
    monkeypatch.setenv("CAULDRON_PODMAN_PATH", "/nonexistent/podman")
    assert podman.exec_in_container("cauldron-foo", "ls") == 1


def test_exec_hook_in_container_streams_output_when_verbose(fake_podman):
    podman.set_verbose(True)
    assert podman.exec_hook_in_container("cauldron-foo", "/script.sh") == 0
    assert fake_podman.calls() == [["exec", "cauldron-foo", "/script.sh"]]


def test_exec_hook_in_container_captures_output_when_not_verbose(fake_podman):
    assert podman.exec_hook_in_container("cauldron-foo", "/script.sh") == 0
    assert fake_podman.calls() == [["exec", "cauldron-foo", "/script.sh"]]


def test_exec_hook_in_container_returns_exit_code(fake_podman):
    fake_podman.respond(["exec"], returncode=7)
    assert podman.exec_hook_in_container("cauldron-foo", "/script.sh") == 7


def test_exec_hook_in_container_returns_podman_not_found(monkeypatch):
    monkeypatch.setenv("CAULDRON_PODMAN_PATH", "/nonexistent/podman")
    assert podman.exec_hook_in_container("cauldron-foo", "/script.sh") == 1


def test_list_containers_parses_podman_ps_json(fake_podman):
    json_output = """[
        {
            "Names": ["cauldron-foo"],
            "Image": "cauldron-foo:latest",
            "State": "running",
            "Ports": [
                {"host_ip": "0.0.0.0", "host_port": 2222, "container_port": 22, "protocol": "tcp"}
            ],
            "Labels": {"cauldron.project_dir": "/home/deck/projects/foo"}
        }
    ]"""
    fake_podman.respond(["ps"], stdout=json_output)
    containers = podman.list_containers()
    assert len(containers) == 1
    assert containers[0]["name"] == "cauldron-foo"
    assert containers[0]["image"] == "cauldron-foo:latest"
    assert containers[0]["status"] == "running"
    assert containers[0]["ports"] == ["0.0.0.0:2222->22/tcp"]
    assert containers[0]["project_dir"] == "/home/deck/projects/foo"


def test_list_containers_formats_ports_with_camel_case_keys(fake_podman):
    json_output = """[
        {
            "Names": ["cauldron-foo"],
            "Image": "cauldron-foo:latest",
            "State": "running",
            "Ports": [{"hostPort": 8080, "containerPort": 8080}]
        }
    ]"""
    fake_podman.respond(["ps"], stdout=json_output)
    containers = podman.list_containers()
    assert containers[0]["ports"] == ["0.0.0.0:8080->8080/tcp"]


def test_list_containers_returns_empty_on_failure(fake_podman):
    fake_podman.respond(["ps"], returncode=1)
    assert podman.list_containers() == []


def test_build_project_image_copies_scripts_and_runs_post_build(tmp_path):
    captured = {"content": None, "args": None}

    def capture_build(_tag, dockerfile, _context, build_args=None):
        captured["content"] = dockerfile.read_text()
        captured["args"] = build_args
        return True

    script_dir = tmp_path / ".cauldron"
    script_dir.mkdir()
    (script_dir / "post_build.sh").write_text("#!/bin/bash\necho build")
    (script_dir / "post-start.sh").write_text("#!/bin/bash\necho start")
    scripts = {"post_build": "post_build.sh", "post_start": "post-start.sh"}

    with patch("cauldron.podman.build_image", side_effect=capture_build):
        assert (
            podman.build_project_image(
                "cauldron-proj:latest",
                "1000",
                "1000",
                scripts=scripts,
                project_dir=tmp_path,
            )
            is True
        )

    content = captured["content"]
    assert "COPY post_build.sh /usr/local/share/cauldron/post_build.sh" in content
    assert "RUN chmod +x /usr/local/share/cauldron/post_build.sh" in content
    assert "COPY post_start.sh /usr/local/share/cauldron/post_start.sh" in content
    assert "USER cauldron" in content
    assert "ARG CAULDRON_RUN_POST_BUILD=false" in content
    assert (
        'RUN if [ "$CAULDRON_RUN_POST_BUILD" = "true" ]; then /usr/local/share/cauldron/post_build.sh; fi'
        in content
    )
    assert captured["args"] == {
        "CAULDRON_BASE": podman.BASE_IMAGE,
        "CAULDRON_RUN_POST_BUILD": "true",
    }


def test_build_project_image_copies_file_scripts(tmp_path):
    captured = {"content": None}

    def capture_build(_tag, dockerfile, _context, build_args=None):
        captured["content"] = dockerfile.read_text()
        return True

    script_file = tmp_path / ".cauldron" / "post-start.sh"
    script_file.parent.mkdir(parents=True)
    script_file.write_text("#!/bin/bash\necho start")

    scripts = {"post_start": "post-start.sh"}

    with patch("cauldron.podman.build_image", side_effect=capture_build):
        assert (
            podman.build_project_image(
                "cauldron-proj:latest",
                "1000",
                "1000",
                scripts=scripts,
                project_dir=tmp_path,
            )
            is True
        )

    content = captured["content"]
    assert "COPY post_start.sh /usr/local/share/cauldron/post_start.sh" in content


def test_build_project_image_sets_entrypoint(tmp_path):
    captured = {"content": None}

    def capture_build(_tag, dockerfile, _context, build_args=None):
        captured["content"] = dockerfile.read_text()
        return True

    script_dir = tmp_path / ".cauldron"
    script_dir.mkdir()
    (script_dir / "entrypoint.sh").write_text('#!/bin/bash\nexec "$@"')
    scripts = {"entrypoint": "entrypoint.sh"}

    with patch("cauldron.podman.build_image", side_effect=capture_build):
        assert (
            podman.build_project_image(
                "cauldron-proj:latest",
                "1000",
                "1000",
                scripts=scripts,
                project_dir=tmp_path,
            )
            is True
        )

    assert (
        'ENTRYPOINT ["/usr/local/share/cauldron/entrypoint.sh"]' in captured["content"]
    )


def test_run_container_appends_sleep_infinity_by_default(fake_podman):
    podman.run_container(
        name="cauldron-foo",
        image="cauldron-foo:latest",
        workdir="/home/deck/projects/foo",
        project_dir="/home/deck/projects/foo",
        uid="1000",
        gid="1000",
    )
    args = fake_podman.calls()[0]
    assert args[-2:] == ["sleep", "infinity"]


def test_run_container_omits_sleep_when_entrypoint_set(fake_podman):
    podman.run_container(
        name="cauldron-foo",
        image="cauldron-foo:latest",
        workdir="/home/deck/projects/foo",
        project_dir="/home/deck/projects/foo",
        uid="1000",
        gid="1000",
        entrypoint=True,
    )
    args = fake_podman.calls()[0]
    assert "sleep" not in args


def test_format_mount_spec_with_options():
    mount = {"source": "/host", "target": "/container", "options": "ro,Z"}
    assert podman._format_mount_spec(mount) == "/host:/container:ro,Z"


def test_format_mount_spec_without_options():
    mount = {"source": "/host", "target": "/container"}
    assert podman._format_mount_spec(mount) == "/host:/container"


def test_has_selinux_option_detects_labels():
    assert podman._has_selinux_option("ro,Z") is True
    assert podman._has_selinux_option("z") is True
    assert podman._has_selinux_option("ro") is False
