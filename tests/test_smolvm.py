"""Contract tests for the smolvm wrapper.

Argument-level behaviour is asserted through the recording fake (see
``conftest.py``). The smolvm backend drives the ``smolvm`` CLI through
subprocess and builds the project image with the host builder (podman/docker),
which is pointed at the same fake for these tests.
"""

import json
import os

from cauldron import smolvm


def _ls_json(*machines):
    return json.dumps(list(machines))


def _machine(name, state="running", image="img", labels=None, ports=0):
    return {
        "name": name,
        "state": state,
        "image": image,
        "labels": labels or {},
        "ports": ports,
    }


def test_set_verbose_changes_flag():
    smolvm.set_verbose(True)
    assert smolvm._verbose is True
    smolvm.set_verbose(False)
    assert smolvm._verbose is False


def test_run_records_argv_without_binary(fake_smolvm):
    result = smolvm._smolvm(["--version"])
    assert result.returncode == 0
    assert fake_smolvm.calls() == [["--version"]]


def test_run_returns_nonzero_when_smolvm_not_found(monkeypatch):
    monkeypatch.setenv("CAULDRON_SMOLVM_PATH", "/nonexistent/smolvm")
    result = smolvm._smolvm(["--version"])
    assert result.returncode == 1


def test_version_true_when_smolvm_present(fake_smolvm):
    assert smolvm.version() is True


def test_version_false_when_smolvm_fails(fake_smolvm):
    fake_smolvm.respond(["--version"], returncode=1)
    assert smolvm.version() is False


def test_kvm_check_passes_when_device_is_writable(monkeypatch):
    monkeypatch.setattr(smolvm, "KVM_DEVICE", "/dev/null")
    assert smolvm.kvm_check() == (True, "")


def test_kvm_check_reports_missing_device(monkeypatch, tmp_path):
    monkeypatch.setattr(smolvm, "KVM_DEVICE", str(tmp_path / "absent-kvm"))
    ok, hint = smolvm.kvm_check()
    assert ok is False
    assert f"{tmp_path / 'absent-kvm'} is missing" in hint
    assert "KVM" in hint


def test_kvm_check_reports_unwritable_device(monkeypatch, tmp_path):
    device = tmp_path / "kvm"
    device.write_text("")
    monkeypatch.setattr(smolvm, "KVM_DEVICE", str(device))
    monkeypatch.setattr(smolvm, "_path_writable", lambda path: False)
    ok, hint = smolvm.kvm_check()
    assert ok is False
    assert f"sudo setfacl -m u:{os.getuid()}:rw {device}" in hint
    assert "user namespaces" in hint


def test_image_tar_path_uses_override_dir_and_sanitizes(tmp_path, monkeypatch):
    monkeypatch.setenv("CAULDRON_SMOLTAR_DIR", str(tmp_path / "tar"))
    path = smolvm.image_tar_path("docker.io/library/debian:trixie-slim")
    assert path == tmp_path / "tar" / "docker.io-library-debian-trixie-slim.tar"


def test_image_exists_checks_tar(fake_smolvm):
    tag = "cauldron-foo:latest"
    tar = smolvm.image_tar_path(tag)
    assert smolvm.image_exists(tag) is False
    tar.parent.mkdir(parents=True, exist_ok=True)
    tar.write_bytes(b"")
    assert smolvm.image_exists(tag) is True


def test_ensure_base_image_pulls_with_builder(fake_smolvm, monkeypatch):
    monkeypatch.setattr(
        smolvm, "_host_builder", lambda: os.environ["CAULDRON_SMOLVM_PATH"]
    )
    fake_smolvm.respond(["pull"], returncode=0)
    assert smolvm.ensure_base_image("base:img") is True
    assert fake_smolvm.calls() == [["pull", "base:img"]]


def test_ensure_base_image_false_without_builder(monkeypatch):
    monkeypatch.setattr(smolvm, "_host_builder", lambda: None)
    assert smolvm.ensure_base_image() is False


def test_build_image_builds_and_tags(fake_smolvm, monkeypatch, tmp_path):
    monkeypatch.setattr(
        smolvm, "_host_builder", lambda: os.environ["CAULDRON_SMOLVM_PATH"]
    )
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM base\n")
    result = smolvm.build_image("tag:1", dockerfile, tmp_path, build_args={"A": "b"})
    assert result.returncode == 0
    assert fake_smolvm.calls() == [
        [
            "build",
            "-t",
            "tag:1",
            "-f",
            str(dockerfile),
            "--build-arg",
            "A=b",
            str(tmp_path),
        ]
    ]


def test_build_image_reports_no_builder(monkeypatch):
    monkeypatch.setattr(smolvm, "_host_builder", lambda: None)
    result = smolvm.build_image("tag:1", "Dockerfile", ".")
    assert result.returncode == 1
    assert "no host builder" in result.stderr


def test_build_project_image_builds_and_saves_tar(fake_smolvm, monkeypatch):
    monkeypatch.setattr(
        smolvm, "_host_builder", lambda: os.environ["CAULDRON_SMOLVM_PATH"]
    )
    fake_smolvm.respond(["build"], returncode=0)
    fake_smolvm.respond(["save"], returncode=0)

    result = smolvm.build_project_image(
        "cauldron-foo:latest", 1000, 1000, base_image="base:img"
    )
    assert result.returncode == 0

    calls = fake_smolvm.calls()
    build_calls = [c for c in calls if c and c[0] == "build"]
    save_calls = [c for c in calls if c and c[0] == "save"]
    assert len(build_calls) == 1
    assert "--build-arg" in build_calls[0]
    assert "CAULDRON_BASE=base:img" in build_calls[0]
    assert len(save_calls) == 1
    assert save_calls[0][1] == "-o"
    assert save_calls[0][-1] == "cauldron-foo:latest"

    final = smolvm.image_tar_path("cauldron-foo:latest")
    tmp = final.with_name(f"{final.name}.tmp")
    # The export targets a fresh sibling that is then renamed into place.
    assert save_calls[0][2] == str(tmp)
    assert final.exists()
    assert not tmp.exists()


def test_build_project_image_overwrites_stale_tar(fake_smolvm, monkeypatch):
    monkeypatch.setattr(
        smolvm, "_host_builder", lambda: os.environ["CAULDRON_SMOLVM_PATH"]
    )
    fake_smolvm.respond(["build"], returncode=0)
    fake_smolvm.respond(["save"], returncode=0)

    final = smolvm.image_tar_path("cauldron-foo:latest")
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"stale-archive")

    result = smolvm.build_project_image(
        "cauldron-foo:latest", 1000, 1000, base_image="base:img"
    )
    assert result.returncode == 0
    assert final.read_bytes() == b"fake-image-archive"
    assert not final.with_name(f"{final.name}.tmp").exists()


def test_container_exists(fake_smolvm):
    fake_smolvm.respond(
        ["machine", "ls", "--json"], stdout=_ls_json(_machine("cauldron-foo"))
    )
    assert smolvm.container_exists("cauldron-foo") is True
    assert smolvm.container_exists("cauldron-other") is False


def test_container_running(fake_smolvm):
    fake_smolvm.respond(
        ["machine", "ls", "--json"],
        stdout=_ls_json(
            _machine("cauldron-foo", state="running"),
            _machine("cauldron-bar", state="stopped"),
        ),
    )
    assert smolvm.container_running("cauldron-foo") is True
    assert smolvm.container_running("cauldron-bar") is False


def test_container_running_false_when_missing(fake_smolvm):
    fake_smolvm.respond(["machine", "ls", "--json"], stdout=_ls_json())
    assert smolvm.container_running("cauldron-foo") is False


def test_container_id(fake_smolvm):
    fake_smolvm.respond(
        ["machine", "ls", "--json"], stdout=_ls_json(_machine("cauldron-foo"))
    )
    assert smolvm.container_id("cauldron-foo") == "cauldron-foo"
    assert smolvm.container_id("cauldron-other") is None


def test_container_mounts_is_empty(fake_smolvm):
    assert smolvm.container_mounts("cauldron-foo") == []


def test_run_container_creates_and_starts(fake_smolvm):
    image = "cauldron-foo:latest"
    tar = smolvm.image_tar_path(image)

    fake_smolvm.respond(["machine", "create"], returncode=0)
    fake_smolvm.respond(["machine", "start"], returncode=0)

    result = smolvm.run_container("cauldron-foo", image, "/proj", "/proj", 1000, 1000)
    assert result.returncode == 0

    create = fake_smolvm.calls()[0]
    assert create[:2] == ["machine", "create"]
    assert "--name" in create and "cauldron-foo" in create
    assert "-I" in create and str(tar) in create
    assert "--label" in create and "cauldron.project_dir=/proj" in create
    assert "--net" in create
    assert "-u" in create and "1000:1000" in create
    assert "-w" in create and "/proj" in create
    assert "/proj:/proj" in create
    assert create[-3:] == ["--", "sleep", "infinity"]
    assert fake_smolvm.calls()[1][:2] == ["machine", "start"]


def test_run_container_omits_sleep_with_entrypoint(fake_smolvm):
    fake_smolvm.respond(["machine", "create"], returncode=0)
    fake_smolvm.respond(["machine", "start"], returncode=0)
    smolvm.run_container(
        "cauldron-foo",
        "cauldron-foo:latest",
        "/proj",
        "/proj",
        1000,
        1000,
        entrypoint=True,
    )
    create = fake_smolvm.calls()[0]
    assert "--" not in create


def test_run_container_adds_volumes_ports_and_env(fake_smolvm):
    fake_smolvm.respond(["machine", "create"], returncode=0)
    fake_smolvm.respond(["machine", "start"], returncode=0)
    smolvm.run_container(
        "cauldron-foo",
        "cauldron-foo:latest",
        "/proj",
        "/proj",
        1000,
        1000,
        env={"FOO": "bar"},
        mounts=[{"source": "/data", "target": "/data", "options": "ro"}],
        ports=["8080:80"],
    )
    create = fake_smolvm.calls()[0]
    assert "-e" in create and "FOO=bar" in create
    assert "/data:/data:ro" in create
    assert "-p" in create and "8080:80" in create


def test_run_container_ignores_devices(fake_smolvm):
    fake_smolvm.respond(["machine", "create"], returncode=0)
    fake_smolvm.respond(["machine", "start"], returncode=0)
    result = smolvm.run_container(
        "cauldron-foo",
        "cauldron-foo:latest",
        "/proj",
        "/proj",
        1000,
        1000,
        devices=["/dev/kvm"],
    )
    assert result.returncode == 0
    create = fake_smolvm.calls()[0]
    assert "--device" not in create
    assert "/dev/kvm" not in create


def test_run_container_expands_path_placeholder(fake_smolvm, monkeypatch):
    monkeypatch.setattr(smolvm, "_image_default_path", lambda tag: "/usr/bin:/bin")
    fake_smolvm.respond(["machine", "create"], returncode=0)
    fake_smolvm.respond(["machine", "start"], returncode=0)
    smolvm.run_container(
        "cauldron-foo",
        "cauldron-foo:latest",
        "/proj",
        "/proj",
        1000,
        1000,
        env={"PATH": "${PATH}/opt/bin"},
    )
    create = next(c for c in fake_smolvm.calls() if c[:2] == ["machine", "create"])
    assert "PATH=/usr/bin:/bin/opt/bin" in create


def test_image_default_path_parses_env(monkeypatch):
    import subprocess

    monkeypatch.setattr(smolvm, "_host_builder", lambda: "podman")
    monkeypatch.setattr(
        smolvm,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout='["PATH=/usr/bin:/bin", "LANG=C"]',
            stderr="",
        ),
    )
    assert smolvm._image_default_path("img:tag") == "/usr/bin:/bin"


def test_image_default_path_empty_when_inspect_fails(monkeypatch):
    import subprocess

    monkeypatch.setattr(smolvm, "_host_builder", lambda: "podman")
    monkeypatch.setattr(
        smolvm,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="not found"
        ),
    )
    assert smolvm._image_default_path("img:tag") == ""


def test_run_container_returns_create_failure(fake_smolvm):
    fake_smolvm.respond(["machine", "create"], returncode=3)
    result = smolvm.run_container(
        "cauldron-foo", "cauldron-foo:latest", "/proj", "/proj", 1000, 1000
    )
    assert result.returncode == 3
    calls = fake_smolvm.calls()
    assert len(calls) == 1
    assert calls[0][:2] == ["machine", "create"]


def test_run_container_appends_known_hosts(fake_smolvm):
    fake_smolvm.respond(["machine", "create"], returncode=0)
    fake_smolvm.respond(["machine", "start"], returncode=0)
    fake_smolvm.respond(["machine", "exec"], returncode=0)
    smolvm.run_container(
        "cauldron-foo",
        "cauldron-foo:latest",
        "/proj",
        "/proj",
        1000,
        1000,
        known_hosts=["1.2.3.4 example.com"],
    )
    exec_calls = [c for c in fake_smolvm.calls() if c[:2] == ["machine", "exec"]]
    assert exec_calls
    assert "1.2.3.4 example.com" in exec_calls[0][-1]
    assert exec_calls[0][0] == "machine"


def test_start_stop_restart_remove(fake_smolvm):
    fake_smolvm.respond(["machine", "start"], returncode=0)
    fake_smolvm.respond(["machine", "stop"], returncode=0)
    fake_smolvm.respond(["machine", "delete"], returncode=0)

    assert smolvm.start_container("cauldron-foo").returncode == 0
    assert smolvm.stop_container("cauldron-foo") is True
    assert smolvm.restart_container("cauldron-foo").returncode == 0
    assert smolvm.remove_container("cauldron-foo") is True
    assert smolvm.remove_container("cauldron-foo", force=True) is True

    calls = fake_smolvm.calls()
    assert any(c[:2] == ["machine", "start"] for c in calls)
    assert any(c[:2] == ["machine", "stop"] for c in calls)
    assert any(c[:2] == ["machine", "delete"] for c in calls)
    assert any(
        c == ["machine", "delete", "--name", "cauldron-foo", "--force"] for c in calls
    )


def test_container_shell_probes_printenv(fake_smolvm):
    fake_smolvm.respond(["machine", "exec"], stdout="/bin/zsh\n")
    assert smolvm.container_shell("cauldron-foo") == "/bin/zsh"
    assert fake_smolvm.calls() == [
        ["machine", "exec", "--name", "cauldron-foo", "--", "printenv", "SHELL"]
    ]


def test_container_shell_falls_back_when_unset(fake_smolvm):
    fake_smolvm.respond(["machine", "exec"], returncode=1)
    assert smolvm.container_shell("cauldron-foo") == "bash"


def test_exec_in_container(fake_smolvm):
    code = smolvm.exec_in_container("cauldron-foo", "ls", args=["-la"])
    assert code == 0
    assert fake_smolvm.calls() == [
        ["machine", "exec", "--name", "cauldron-foo", "--", "ls", "-la"]
    ]


def test_exec_in_container_interactive_tty(fake_smolvm):
    smolvm.exec_in_container("cauldron-foo", "bash", interactive=True, tty=True)
    assert fake_smolvm.calls() == [
        ["machine", "exec", "--name", "cauldron-foo", "-i", "-t", "--", "bash"]
    ]


def test_exec_hook_in_container(fake_smolvm):
    assert (
        smolvm.exec_hook_in_container(
            "cauldron-foo", "/usr/local/share/cauldron/hook.sh"
        )
        == 0
    )
    assert fake_smolvm.calls() == [
        [
            "machine",
            "exec",
            "--name",
            "cauldron-foo",
            "--",
            "/usr/local/share/cauldron/hook.sh",
        ]
    ]


def test_exec_check_with_user(fake_smolvm):
    assert smolvm.exec_check("cauldron-foo", ["test", "-f", "/x"], user="0") is True
    assert fake_smolvm.calls() == [
        [
            "machine",
            "exec",
            "--name",
            "cauldron-foo",
            "--user",
            "0",
            "--",
            "test",
            "-f",
            "/x",
        ]
    ]


def test_exec_capture(fake_smolvm):
    fake_smolvm.respond(["machine", "exec"], stdout="hello\n")
    assert smolvm.exec_capture("cauldron-foo", ["echo", "hello"]) == "hello"


def test_exec_with_stdin(fake_smolvm):
    assert smolvm.exec_with_stdin("cauldron-foo", ["cat"], "data\n", user="0") is True
    assert fake_smolvm.calls() == [
        ["machine", "exec", "--name", "cauldron-foo", "-i", "--user", "0", "--", "cat"]
    ]


def test_list_containers_filters_cauldron_machines(fake_smolvm):
    fake_smolvm.respond(
        ["machine", "ls", "--json"],
        stdout=_ls_json(
            _machine(
                "cauldron-foo",
                labels={"cauldron.project_dir": "/proj"},
                ports=1,
            ),
            _machine("other-machine"),
        ),
    )
    listed = smolvm.list_containers()
    assert [c["name"] for c in listed] == ["cauldron-foo"]
    assert listed[0]["project_dir"] == "/proj"
    assert listed[0]["ports"] == ["1"]
    assert listed[0]["host_ports"] == []


def test_list_containers_running_only_by_default(fake_smolvm):
    fake_smolvm.respond(
        ["machine", "ls", "--json"],
        stdout=_ls_json(
            _machine("cauldron-running", state="running"),
            _machine("cauldron-stopped", state="stopped"),
        ),
    )
    assert [c["name"] for c in smolvm.list_containers()] == ["cauldron-running"]
    assert [c["name"] for c in smolvm.list_containers(all_containers=True)] == [
        "cauldron-running",
        "cauldron-stopped",
    ]


def test_list_containers_empty_on_failure(fake_smolvm):
    fake_smolvm.respond(["machine", "ls", "--json"], returncode=1)
    assert smolvm.list_containers() == []


def test_format_port():
    assert smolvm._format_port("8080:80") == "8080:80"
    assert smolvm._format_port("127.0.0.1:8080:80") == "8080:80"
    assert smolvm._format_port("8080:80/tcp") == "8080:80/tcp"
    assert smolvm._format_port("80") == "80:80"


def test_format_volume():
    assert smolvm._format_volume({"source": "/a", "target": "/b"}) == "/a:/b"
    assert (
        smolvm._format_volume({"source": "/a", "target": "/b", "options": "ro"})
        == "/a:/b:ro"
    )


def test_port_in_use_detection():
    assert smolvm.is_port_in_use_error("Error: address already in use")
    assert smolvm.is_port_in_use_error("") is False
    assert smolvm.port_from_error("bind 0.0.0.0:8080: address already in use") == 8080
    assert smolvm.port_from_error("nope") is None


def test_find_port_owner_returns_empty(fake_smolvm):
    # smolvm does not expose host:guest mappings, so a port can't be attributed
    # to a specific machine.
    assert smolvm.find_port_owner(8080) == []
    assert smolvm.find_port_owner(None) == []


def test_ssh_proxy_command(fake_smolvm):
    command = smolvm.ssh_proxy_command("cauldron-foo")
    assert "machine exec --name cauldron-foo" in command
    assert "--user 0 -i --" in command
    assert "/usr/sbin/sshd -i" in command
