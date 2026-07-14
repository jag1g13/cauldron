import pathlib
import tomllib

from cauldron.project import CONFIG_GLOBAL_PATH, CONFIG_PROJECT_PATH


def _load(path):
    """Load a TOML file, returning an empty dict if it is missing."""
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_config(project_dir=None):
    """Load merged global and project configuration.

    Project config overrides global config. Only the ``env`` table is merged
    at the key level; project env values replace global env values.
    """
    config = _load(CONFIG_GLOBAL_PATH)
    project_path = pathlib.Path(project_dir or ".").resolve() / CONFIG_PROJECT_PATH
    project_config = _load(project_path)

    if "env" in project_config:
        config.setdefault("env", {}).update(project_config["env"])

    return config


def container_env(config):
    """Return environment variables from config to set in the container.

    Values must be strings; non-string values are converted to strings.
    """
    env = {}
    for key, value in config.get("env", {}).items():
        env[key] = value if isinstance(value, str) else str(value)
    return env
