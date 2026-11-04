"""Tests for the recording podman double and the ``CAULDRON_PODMAN_PATH`` seam.

These cover argument-level behaviour that cannot be observed with a real Podman
service, such as the exact flags passed to ``podman run``.
"""

from cauldron import podman


def test_fake_records_argv_and_reports_success(fake_podman):
    assert podman.image_exists("example:latest") is True
    assert fake_podman.calls() == [["image", "exists", "example:latest"]]


def test_fake_returncode_controls_result(fake_podman):
    fake_podman.respond(["container", "exists"], returncode=1)
    assert podman.container_exists("cauldron-foo") is False


def test_fake_stdout_drives_output_parsing(fake_podman):
    fake_podman.respond(["image", "inspect"], stdout='["PATH=/usr/bin:/bin"]')
    assert podman.image_env("example:latest") == {"PATH": "/usr/bin:/bin"}


def test_fake_success_by_default_when_unmatched(fake_podman):
    assert podman.version() is True


def test_fake_container_running_reads_stdout(fake_podman):
    fake_podman.respond(["container", "inspect"], stdout="true\n")
    assert podman.container_running("cauldron-foo") is True


def test_fake_captures_run_container_arguments(fake_podman):
    assert (
        podman.run_container(
            name="cauldron-foo",
            image="cauldron-foo:latest",
            workdir="/home/deck/projects/foo",
            project_dir="/home/deck/projects/foo",
            uid="1000",
            gid="1000",
            env={"EDITOR": "vim"},
            mounts=[{"source": "/host/extra", "target": "/extra", "options": "ro"}],
            ports=["8080:8080"],
            known_hosts=["my-service.local:127.0.0.1"],
        )
        is True
    )

    argv = fake_podman.calls()[0]
    assert argv[0] == "run"
    assert "cauldron-foo" in argv
    assert "1000:1000" in argv
    assert "EDITOR=vim" in argv
    assert "/host/extra:/extra:ro" in argv
    assert "8080:8080" in argv
    assert "my-service.local:127.0.0.1" in argv
    assert argv[-2:] == ["sleep", "infinity"]
