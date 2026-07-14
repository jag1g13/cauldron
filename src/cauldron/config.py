import pathlib
import tomllib
import warnings

from cauldron.project import CONFIG_GLOBAL_PATH, CONFIG_PROJECT_PATH


def _load(path):
    """Load a TOML file, returning an empty dict if it is missing."""
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _normalize_mount(item):
    """Convert a mount entry into a dict with source, target and options."""
    if isinstance(item, str):
        parts = item.split(":")
        if len(parts) < 2:
            raise ValueError(f"Invalid mount string: {item!r}")
        return {
            "source": parts[0],
            "target": parts[1],
            "options": ":".join(parts[2:]) if len(parts) > 2 else "",
        }
    if isinstance(item, dict):
        try:
            source = item["source"]
            target = item["target"]
        except KeyError as exc:
            raise ValueError(f"Mount is missing required key: {exc.args[0]}") from exc
        return {
            "source": source,
            "target": target,
            "options": item.get("options", ""),
        }
    raise ValueError(f"Mount must be a string or table, got {type(item).__name__}")


def _format_mount(mount):
    """Format a normalized mount dict for Podman's -v flag."""
    if mount["options"]:
        return f"{mount['source']}:{mount['target']}:{mount['options']}"
    return f"{mount['source']}:{mount['target']}"


def _merge_mounts(global_mounts, project_mounts, warn):
    """Merge mount lists by target, project overrides global."""
    seen = {}

    def _add(source_name, item):
        mount = _normalize_mount(item)
        target = mount["target"]
        if target in seen:
            previous_source, _ = seen[target]
            if previous_source == "global" and source_name == "project":
                warn(f"Project mount overrides global mount for target {target!r}")
            elif previous_source == source_name:
                warn(f"Duplicate {source_name} mount for target {target!r}")
            else:
                warn(f"Mount for target {target!r} is defined more than once")
        seen[target] = (source_name, mount)

    for item in global_mounts:
        _add("global", item)
    for item in project_mounts:
        _add("project", item)

    return [mount for _, mount in seen.values()]


def _port_key(port):
    """Return a merge key for a port forwarding string."""
    if not isinstance(port, str):
        raise ValueError(f"Port must be a string, got {type(port).__name__}")
    parts = port.split(":")
    # Podman short syntax: [host_ip:][host_port:]container_port[/protocol]
    if len(parts) >= 3:
        return parts[1]
    return parts[0]


def _merge_ports(global_ports, project_ports, warn):
    """Merge port lists by host port, project overrides global."""
    seen = {}

    def _add(source_name, port):
        key = _port_key(port)
        if key in seen:
            previous_source, _ = seen[key]
            if previous_source == "global" and source_name == "project":
                warn(f"Project port overrides global port for host port {key!r}")
            elif previous_source == source_name:
                warn(f"Duplicate {source_name} port for host port {key!r}")
            else:
                warn(f"Port for host port {key!r} is defined more than once")
        seen[key] = (source_name, port)

    for port in global_ports:
        _add("global", port)
    for port in project_ports:
        _add("project", port)

    return [port for _, port in seen.values()]


def load_config(project_dir=None, warn=None):
    """Load merged global and project configuration.

    Project config overrides global config. The ``env`` table is merged at the
    key level. The ``container`` table is merged key-by-key, but ``mounts`` are
    merged by ``target`` and ``ports`` are merged by host port so that project
    values override global values without discarding unrelated entries.
    """
    if warn is None:
        warn = warnings.warn

    config = _load(CONFIG_GLOBAL_PATH)
    project_path = pathlib.Path(project_dir or ".").resolve() / CONFIG_PROJECT_PATH
    project_config = _load(project_path)

    if "env" in project_config:
        config.setdefault("env", {}).update(project_config["env"])

    global_container = config.get("container", {})
    project_container = project_config.get("container", {})

    # Merge simple container keys directly.
    for key, value in project_container.items():
        if key not in ("mounts", "ports"):
            global_container[key] = value

    if "mounts" in global_container or "mounts" in project_container:
        global_container["mounts"] = _merge_mounts(
            global_container.get("mounts", []),
            project_container.get("mounts", []),
            warn,
        )

    if "ports" in global_container or "ports" in project_container:
        global_container["ports"] = _merge_ports(
            global_container.get("ports", []),
            project_container.get("ports", []),
            warn,
        )

    if global_container:
        config["container"] = global_container

    return config


def base_image(config):
    """Return the configured base image, or None to use the default."""
    return config.get("container", {}).get("base_image")


def container_env(config):
    """Return environment variables from config to set in the container.

    Values must be strings; non-string values are converted to strings.
    """
    env = {}
    for key, value in config.get("env", {}).items():
        env[key] = value if isinstance(value, str) else str(value)
    return env


def container_mounts(config):
    """Return normalized mount dicts for the container runtime.

    Each dict contains ``source``, ``target`` and ``options`` keys. Use
    :func:`_format_mount` to convert a dict into Podman's ``-v`` syntax.
    """
    return [
        _normalize_mount(item) for item in config.get("container", {}).get("mounts", [])
    ]


def container_ports(config):
    """Return port forwarding strings for the container runtime."""
    return list(config.get("container", {}).get("ports", []))
