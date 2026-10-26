import pathlib
from unittest.mock import patch

import pytest

from cauldron import podman_remote


def test_asset_names_arch_suffixed_for_modern_versions():
    names = podman_remote._asset_names("5.4.2", "amd64")
    assert names[0] == "podman-remote-static-linux_amd64.tar.gz"
    assert "podman-remote-static.tar.gz" in names


def test_asset_names_neutral_for_old_versions():
    names = podman_remote._asset_names("3.4.4", "amd64")
    assert names[0] == "podman-remote-static.tar.gz"


def test_server_version_strips_leading_v(fake_podman):
    fake_podman.respond(["version"], stdout="v3.4.4\n")
    assert podman_remote.server_version() == "3.4.4"


def test_server_version_returns_none_when_unavailable(fake_podman):
    fake_podman.respond(["version"], returncode=1)
    assert podman_remote.server_version() is None


@pytest.mark.integration
def test_server_version_against_live_service(podman_service):
    version = podman_remote.server_version()
    assert version


def test_ensure_socket_raises_when_missing(tmp_path):
    with patch(
        "cauldron.podman_remote.socket_path", return_value=tmp_path / "missing.sock"
    ):
        with pytest.raises(podman_remote.RemoteError, match="systemctl --user"):
            podman_remote.ensure_socket()


def test_ensure_socket_returns_path_when_present(tmp_path):
    socket = tmp_path / "podman.sock"
    socket.write_text("")
    with patch("cauldron.podman_remote.socket_path", return_value=socket):
        assert podman_remote.ensure_socket() == socket


def test_ensure_client_uses_cached_binary(tmp_path, monkeypatch, fake_podman):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    directory = podman_remote._cache_dir("5.4.2")
    directory.mkdir(parents=True)
    binary = directory / "podman"
    binary.write_text("cached")

    fake_podman.respond(["version"], stdout="5.4.2\n")
    assert podman_remote.ensure_client() == binary


def test_ensure_client_downloads_and_extracts(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    version = "5.4.2"

    def fake_download(_url, destination):
        pathlib.Path(destination).write_bytes(b"archive")
        return destination

    class FakeArchive:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extractall(self, directory, filter=None):
            path = pathlib.Path(directory) / "podman"
            path.write_text("binary")
            path.chmod(0o644)

    with patch("cauldron.podman_remote._fetch_checksums", return_value={}):
        with patch("cauldron.podman_remote._download", side_effect=fake_download):
            with patch(
                "cauldron.podman_remote.tarfile.open", return_value=FakeArchive()
            ):
                binary = podman_remote.ensure_client(version=version, arch="amd64")

    assert binary == podman_remote._cache_dir(version) / "podman"
    assert binary.exists()
    assert binary.stat().st_mode & 0o111


@pytest.mark.slow
def test_ensure_client_downloads_real_binary(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    binary = podman_remote.ensure_client(version="5.4.2", arch="amd64")
    assert binary.exists()
    assert binary.stat().st_mode & 0o111


def test_running_in_remote_false_without_container_host(monkeypatch):
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    assert podman_remote.running_in_remote() is False


def test_running_in_remote_true_with_container_host(monkeypatch):
    monkeypatch.setenv("CONTAINER_HOST", "unix:///run/podman-host/podman.sock")
    assert podman_remote.running_in_remote() is True


def test_container_id_prefers_override(monkeypatch):
    monkeypatch.setenv("CAULDRON_CONTAINER_ID", "override-id")
    monkeypatch.setenv("HOSTNAME", "90fdf395e0ea")
    assert podman_remote._container_id() == "override-id"


def test_container_id_falls_back_to_hostname(monkeypatch):
    monkeypatch.delenv("CAULDRON_CONTAINER_ID", raising=False)
    monkeypatch.setenv("HOSTNAME", "90fdf395e0ea")
    assert podman_remote._container_id() == "90fdf395e0ea"


def test_container_id_uses_system_hostname(monkeypatch):
    monkeypatch.delenv("CAULDRON_CONTAINER_ID", raising=False)
    monkeypatch.delenv("HOSTNAME", raising=False)
    monkeypatch.setattr(
        "cauldron.podman_remote.socket.gethostname", lambda: "90fdf395e0ea"
    )
    assert podman_remote._container_id() == "90fdf395e0ea"


def test_host_backend_paths_reuses_mounts(monkeypatch):
    monkeypatch.setenv("CAULDRON_CONTAINER_ID", "abc")
    monkeypatch.setenv("CONTAINER_HOST", "unix:///run/podman-host/podman.sock")
    monkeypatch.setattr(
        "cauldron.podman.container_mounts",
        lambda name: [
            {"Source": "/run/user/1000/podman", "Destination": "/run/podman-host"},
            {
                "Source": "/home/host/.cache/cauldron/podman-remote/5.4.2/podman",
                "Destination": "/usr/local/bin/cauldron-podman",
            },
        ],
    )

    socket, client = podman_remote.host_backend_paths()

    assert socket == pathlib.Path("/run/user/1000/podman/podman.sock")
    assert client == pathlib.Path(
        "/home/host/.cache/cauldron/podman-remote/5.4.2/podman"
    )


def test_host_backend_paths_raises_without_mounts(monkeypatch):
    monkeypatch.setenv("CAULDRON_CONTAINER_ID", "abc")
    monkeypatch.setattr("cauldron.podman.container_mounts", lambda name: [])

    with pytest.raises(podman_remote.RemoteError, match="podman_socket"):
        podman_remote.host_backend_paths()


def test_host_sibling_mounts_reuses_host_paths_and_skips_managed(monkeypatch):
    monkeypatch.setenv("CAULDRON_CONTAINER_ID", "abc")
    monkeypatch.setattr(
        "cauldron.podman.container_mounts",
        lambda name: [
            {"Source": "/proj", "Destination": "/proj", "RW": True},
            {
                "Source": "/run/user/1000/podman",
                "Destination": "/run/podman-host",
                "RW": True,
            },
            {
                "Source": "/cache/podman",
                "Destination": "/usr/local/bin/cauldron-podman",
                "RW": False,
            },
            {
                "Source": "/host/.gitconfig",
                "Destination": "/home/cauldron/.gitconfig",
                "RW": False,
            },
            {
                "Source": "/run/user/1000/keyring/ssh",
                "Destination": "/run/user/1000/keyring/ssh",
                "RW": False,
            },
        ],
    )

    mounts = podman_remote.host_sibling_mounts("/proj")

    assert mounts == [
        {
            "source": "/host/.gitconfig",
            "target": "/home/cauldron/.gitconfig",
            "options": "ro",
        },
        {
            "source": "/run/user/1000/keyring/ssh",
            "target": "/run/user/1000/keyring/ssh",
            "options": "ro",
        },
    ]


def test_host_sibling_env_drops_managed_keys(monkeypatch):
    monkeypatch.setenv("CAULDRON_CONTAINER_ID", "abc")
    monkeypatch.setattr(
        "cauldron.podman.container_env_of",
        lambda name: {
            "HOME": "/home/cauldron",
            "CONTAINER_HOST": "unix:///run/podman-host/podman.sock",
            "CAULDRON_PODMAN_PATH": "/usr/local/bin/cauldron-podman",
            "SSH_AUTH_SOCK": "/run/user/1000/keyring/ssh",
            "SHELL": "/bin/bash",
        },
    )

    assert podman_remote.host_sibling_env() == {
        "SSH_AUTH_SOCK": "/run/user/1000/keyring/ssh",
        "SHELL": "/bin/bash",
    }
