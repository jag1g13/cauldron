import pytest
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


def test_load_config_merges_container_table(tmp_path, monkeypatch):
    global_file = tmp_path / "home" / "cauldron.toml"
    global_file.parent.mkdir()
    global_file.write_text("""
[container]
base_image = "global/base:latest"
""")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project_file = project_dir / "cauldron.toml"
    project_file.write_text("""
[container]
base_image = "project/base:latest"
""")

    monkeypatch.setattr(config, "CONFIG_GLOBAL_PATH", global_file)
    monkeypatch.setattr(config, "CONFIG_PROJECT_PATH", "cauldron.toml")
    loaded = config.load_config(project_dir)
    assert loaded["container"]["base_image"] == "project/base:latest"


def test_base_image_returns_configured_value():
    cfg = {"container": {"base_image": "astral/uv:python3.14-trixie"}}
    assert config.base_image(cfg) == "astral/uv:python3.14-trixie"


def test_base_image_returns_none_when_missing():
    assert config.base_image({}) is None


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


def test_load_config_merges_container_mounts_by_target(tmp_path, monkeypatch):
    global_file = tmp_path / "home" / "cauldron.toml"
    global_file.parent.mkdir()
    global_file.write_text("""
[container]
mounts = [
  {source = "/global/src", target = "/shared", options = "ro"},
  {source = "/global/only", target = "/global-only", options = "ro"},
]
""")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project_file = project_dir / "cauldron.toml"
    project_file.write_text("""
[container]
mounts = [
  {source = "/project/src", target = "/shared", options = "rw"},
]
""")

    monkeypatch.setattr(config, "CONFIG_GLOBAL_PATH", global_file)
    monkeypatch.setattr(config, "CONFIG_PROJECT_PATH", "cauldron.toml")
    loaded = config.load_config(project_dir, warn=lambda _msg: None)

    mounts = loaded["container"]["mounts"]
    by_target = {m["target"]: m for m in mounts}
    assert by_target["/shared"]["source"] == "/project/src"
    assert by_target["/shared"]["options"] == "rw"
    assert by_target["/global-only"]["source"] == "/global/only"


def test_load_config_warns_on_mount_target_overlap(tmp_path, monkeypatch):
    global_file = tmp_path / "home" / "cauldron.toml"
    global_file.parent.mkdir()
    global_file.write_text("""
[container]
mounts = [
  {source = "/global/src", target = "/shared", options = "ro"},
]
""")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project_file = project_dir / "cauldron.toml"
    project_file.write_text("""
[container]
mounts = [
  {source = "/project/src", target = "/shared", options = "rw"},
]
""")

    monkeypatch.setattr(config, "CONFIG_GLOBAL_PATH", global_file)
    monkeypatch.setattr(config, "CONFIG_PROJECT_PATH", "cauldron.toml")

    warnings = []
    config.load_config(project_dir, warn=warnings.append)
    assert any("overrides global mount" in w for w in warnings)


def test_load_config_accepts_mount_strings(tmp_path, monkeypatch):
    global_file = tmp_path / "home" / "cauldron.toml"
    global_file.parent.mkdir()
    global_file.write_text("""
[container]
mounts = [
  "/host:/container:ro,Z",
]
""")

    monkeypatch.setattr(config, "CONFIG_GLOBAL_PATH", global_file)
    monkeypatch.setattr(config, "CONFIG_PROJECT_PATH", "cauldron.toml")
    with patch("cauldron.config.pathlib.Path.cwd", return_value=tmp_path / "project"):
        loaded = config.load_config(warn=lambda _msg: None)

    mount = loaded["container"]["mounts"][0]
    assert mount["source"] == "/host"
    assert mount["target"] == "/container"
    assert mount["options"] == "ro,Z"


def test_container_mounts_returns_normalized_mounts():
    cfg = {
        "container": {
            "mounts": [
                {"source": "/a", "target": "/b", "options": "ro"},
                "/c:/d:rw,Z",
            ]
        }
    }
    mounts = config.container_mounts(cfg)
    assert mounts == [
        {"source": "/a", "target": "/b", "options": "ro"},
        {"source": "/c", "target": "/d", "options": "rw,Z"},
    ]


def test_container_mounts_returns_empty_when_missing():
    assert config.container_mounts({}) == []


def test_container_mounts_expands_host_vars(monkeypatch):
    monkeypatch.setenv("HOME", "/home/deck")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/run/user/1000/keyring/ssh")
    cfg = {
        "container": {
            "mounts": [
                {
                    "source": "$HOME/.gitconfig",
                    "target": "/home/cauldron/.gitconfig",
                    "options": "ro,Z",
                },
                {
                    "source": "$SSH_AUTH_SOCK",
                    "target": "$SSH_AUTH_SOCK",
                    "options": "ro",
                },
            ]
        }
    }
    mounts = config.container_mounts(cfg)
    assert mounts[0]["source"] == "/home/deck/.gitconfig"
    assert mounts[0]["target"] == "/home/cauldron/.gitconfig"
    assert mounts[1]["source"] == "/run/user/1000/keyring/ssh"
    assert mounts[1]["target"] == "/run/user/1000/keyring/ssh"


def test_container_env_expands_host_vars(monkeypatch):
    monkeypatch.setenv("SSH_AUTH_SOCK", "/run/user/1000/keyring/ssh")
    cfg = {"env": {"SSH_AUTH_SOCK": "$SSH_AUTH_SOCK", "EDITOR": "vim"}}
    assert config.container_env(cfg) == {
        "SSH_AUTH_SOCK": "/run/user/1000/keyring/ssh",
        "EDITOR": "vim",
    }


def test_container_env_does_not_expand_path(monkeypatch):
    monkeypatch.setenv("PATH", "/host/bin")
    cfg = {"env": {"PATH": "/home/cauldron/.local/bin:${PATH}"}}
    assert config.container_env(cfg) == {"PATH": "/home/cauldron/.local/bin:${PATH}"}


def test_load_config_merges_container_ports(tmp_path, monkeypatch):
    global_file = tmp_path / "home" / "cauldron.toml"
    global_file.parent.mkdir()
    global_file.write_text("""
[container]
ports = ["8080:8080", "3000:3000"]
""")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project_file = project_dir / "cauldron.toml"
    project_file.write_text("""
[container]
ports = ["8080:8081"]
""")

    monkeypatch.setattr(config, "CONFIG_GLOBAL_PATH", global_file)
    monkeypatch.setattr(config, "CONFIG_PROJECT_PATH", "cauldron.toml")
    loaded = config.load_config(project_dir, warn=lambda _msg: None)

    ports = loaded["container"]["ports"]
    assert "8080:8081" in ports
    assert "3000:3000" in ports
    assert "8080:8080" not in ports


def test_container_ports_returns_configured_ports():
    cfg = {"container": {"ports": ["8080:8080", "127.0.0.1:3000:3000"]}}
    assert config.container_ports(cfg) == ["8080:8080", "127.0.0.1:3000:3000"]


def test_container_ports_returns_empty_when_missing():
    assert config.container_ports({}) == []


def test_load_config_merges_known_hosts_by_hostname(tmp_path, monkeypatch):
    global_file = tmp_path / "home" / "cauldron.toml"
    global_file.parent.mkdir()
    global_file.write_text("""
[container]
known_hosts = ["my-service.local:127.0.0.1", "registry.internal:10.0.0.5"]
""")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project_file = project_dir / "cauldron.toml"
    project_file.write_text("""
[container]
known_hosts = ["my-service.local:192.168.1.1"]
""")

    monkeypatch.setattr(config, "CONFIG_GLOBAL_PATH", global_file)
    monkeypatch.setattr(config, "CONFIG_PROJECT_PATH", "cauldron.toml")
    loaded = config.load_config(project_dir, warn=lambda _msg: None)

    hosts = loaded["container"]["known_hosts"]
    assert "my-service.local:192.168.1.1" in hosts
    assert "registry.internal:10.0.0.5" in hosts
    assert "my-service.local:127.0.0.1" not in hosts


def test_load_config_warns_on_known_host_overlap(tmp_path, monkeypatch):
    global_file = tmp_path / "home" / "cauldron.toml"
    global_file.parent.mkdir()
    global_file.write_text("""
[container]
known_hosts = ["my-service.local:127.0.0.1"]
""")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project_file = project_dir / "cauldron.toml"
    project_file.write_text("""
[container]
known_hosts = ["my-service.local:192.168.1.1"]
""")

    monkeypatch.setattr(config, "CONFIG_GLOBAL_PATH", global_file)
    monkeypatch.setattr(config, "CONFIG_PROJECT_PATH", "cauldron.toml")

    warnings = []
    config.load_config(project_dir, warn=warnings.append)
    assert any("overrides global known host" in w for w in warnings)


def test_load_config_rejects_known_host_without_colon(tmp_path, monkeypatch):
    global_file = tmp_path / "home" / "cauldron.toml"
    global_file.parent.mkdir()
    global_file.write_text("""
[container]
known_hosts = ["invalid-entry"]
""")

    monkeypatch.setattr(config, "CONFIG_GLOBAL_PATH", global_file)
    monkeypatch.setattr(config, "CONFIG_PROJECT_PATH", "cauldron.toml")
    with pytest.raises(ValueError, match="Invalid known host entry"):
        config.load_config(warn=lambda _msg: None)


def test_container_known_hosts_returns_configured_entries():
    cfg = {"container": {"known_hosts": ["my-service.local:127.0.0.1"]}}
    assert config.container_known_hosts(cfg) == ["my-service.local:127.0.0.1"]


def test_container_known_hosts_returns_empty_when_missing():
    assert config.container_known_hosts({}) == []
