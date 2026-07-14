from unittest.mock import patch

from cauldron import config


def test_load_config_returns_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_GLOBAL_PATH", tmp_path / "missing.toml")
    monkeypatch.setattr(config, "CONFIG_PROJECT_PATH", "cauldron.toml")
    with patch("cauldron.config.pathlib.Path.cwd", return_value=tmp_path):
        assert config.load_config() == {}


def test_load_config_reads_global_file(tmp_path, monkeypatch):
    global_file = tmp_path / "cauldron.toml"
    global_file.write_text("""
[env]
PATH = "/global/bin:${PATH}"
""")
    monkeypatch.setattr(config, "CONFIG_GLOBAL_PATH", global_file)
    monkeypatch.setattr(config, "CONFIG_PROJECT_PATH", "cauldron.toml")
    with patch("cauldron.config.pathlib.Path.cwd", return_value=tmp_path / "project"):
        loaded = config.load_config()
    assert loaded["env"]["PATH"] == "/global/bin:${PATH}"


def test_load_config_project_env_overrides_global(tmp_path, monkeypatch):
    global_file = tmp_path / "home" / "cauldron.toml"
    global_file.parent.mkdir()
    global_file.write_text("""
[env]
PATH = "/global/bin:${PATH}"
EDITOR = "vim"
""")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project_file = project_dir / "cauldron.toml"
    project_file.write_text("""
[env]
PATH = "/project/bin:${PATH}"
""")

    monkeypatch.setattr(config, "CONFIG_GLOBAL_PATH", global_file)
    monkeypatch.setattr(config, "CONFIG_PROJECT_PATH", "cauldron.toml")
    loaded = config.load_config(project_dir)
    assert loaded["env"]["PATH"] == "/project/bin:${PATH}"
    assert loaded["env"]["EDITOR"] == "vim"


def test_container_env_extracts_strings():
    cfg = {"env": {"PATH": "/extra/bin:${PATH}", "EDITOR": "vim"}}
    assert config.container_env(cfg) == {
        "PATH": "/extra/bin:${PATH}",
        "EDITOR": "vim",
    }


def test_container_env_converts_non_strings():
    cfg = {"env": {"PORT": 8080}}
    assert config.container_env(cfg) == {"PORT": "8080"}


def test_container_env_returns_empty_without_env_section():
    assert config.container_env({}) == {}
