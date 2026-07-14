import os
from unittest.mock import patch

from cauldron import project


def test_container_name_uses_directory_name():
    with patch("cauldron.project.pathlib.Path") as mock_path:
        mock_path.return_value.resolve.return_value.name = "myproject"
        assert project.container_name() == "cauldron-myproject"


def test_container_name_uses_override():
    assert project.container_name(override="custom-name") == "custom-name"


def test_project_image_name_uses_directory_name():
    with patch("cauldron.project.pathlib.Path") as mock_path:
        mock_path.return_value.resolve.return_value.name = "myproject"
        assert project.project_image_name() == "cauldron-myproject:latest"


def test_find_dockerfile_prefers_project_file(tmp_path):
    project_file = tmp_path / ".cauldron" / "Dockerfile"
    project_file.parent.mkdir(parents=True)
    project_file.write_text("ARG CAULDRON_BASE\nFROM ${CAULDRON_BASE}\n")

    with patch("cauldron.project.pathlib.Path.cwd", return_value=tmp_path):
        found = project.find_dockerfile()
        assert found == project_file


def test_find_dockerfile_falls_back_to_global(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    global_file = home / ".config" / "cauldron" / "Dockerfile"
    global_file.parent.mkdir(parents=True)
    global_file.write_text("ARG CAULDRON_BASE\nFROM ${CAULDRON_BASE}\n")

    monkeypatch.setattr(project, "DOCKERFILE_GLOBAL_PATH", global_file)
    monkeypatch.setattr(project.pathlib.Path, "cwd", lambda: tmp_path / "project")

    found = project.find_dockerfile()
    assert found == global_file


def test_find_dockerfile_returns_none_when_missing(tmp_path):
    with patch("cauldron.project.pathlib.Path.cwd", return_value=tmp_path):
        assert project.find_dockerfile() is None


def test_host_user_returns_strings():
    uid, gid = project.host_user()
    assert uid == str(os.getuid())
    assert gid == str(os.getgid())
