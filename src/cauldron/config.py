import os
import pathlib
import re
import tomllib
import warnings

from cauldron.project import CONFIG_GLOBAL_PATH, CONFIG_PROJECT_PATH


def _expand_host_vars(value):
    """Expand host environment variables in a string.

    ``$HOME``, ``${HOME}``, ``$SSH_AUTH_SOCK`` and similar references are
    replaced with the corresponding values from the host environment. This
    lets config files use portable paths without hard-coding the user's
    home directory or UID.
    """
    if not isinstance(value, str):
        return value
    return os.path.expandvars(value)


KNOWN_HOOKS = ("post_build", "post_start", "entrypoint")


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


def _known_host_key(entry):
    """Return the hostname merge key for a known-hosts entry string."""
    if not isinstance(entry, str):
        raise ValueError(
            f"Known host entry must be a string, got {type(entry).__name__}"
        )
    if ":" not in entry:
        raise ValueError(
            f"Invalid known host entry: {entry!r} (expected 'hostname:ip')"
        )
    return entry.split(":", 1)[0]


def _device_key(device):
    """Return the merge key (host path) for a device passthrough string."""
    if not isinstance(device, str):
        raise ValueError(f"Device must be a string, got {type(device).__name__}")
    return device.split(":", 1)[0]


def _merge_devices(global_devices, project_devices, warn):
    """Merge device lists by host path, project overrides global."""
    seen = {}

    def _add(source_name, device):
        key = _device_key(device)
        if key in seen:
            previous_source, _ = seen[key]
            if previous_source == "global" and source_name == "project":
                warn(f"Project device overrides global device for path {key!r}")
            elif previous_source == source_name:
                warn(f"Duplicate {source_name} device for path {key!r}")
            else:
                warn(f"Device for path {key!r} is defined more than once")
        seen[key] = (source_name, device)

    for device in global_devices:
        _add("global", device)
    for device in project_devices:
        _add("project", device)

    return [device for _, device in seen.values()]


def _merge_known_hosts(global_hosts, project_hosts, warn):
    """Merge known-hosts lists by hostname, project overrides global."""
    seen = {}

    def _add(source_name, entry):
        key = _known_host_key(entry)
        if key in seen:
            previous_source, _ = seen[key]
            if previous_source == "global" and source_name == "project":
                warn(
                    f"Project known host overrides global known host for hostname {key!r}"
                )
            elif previous_source == source_name:
                warn(f"Duplicate {source_name} known host for hostname {key!r}")
            else:
                warn(f"Known host for hostname {key!r} is defined more than once")
        seen[key] = (source_name, entry)

    for entry in global_hosts:
        _add("global", entry)
    for entry in project_hosts:
        _add("project", entry)

    return [entry for _, entry in seen.values()]


def _merge_backend(global_backend, project_backend, warn):
    """Merge ``[backend]`` tables; project overrides global by key.

    Nested per-provider tables (e.g. ``[backend.smolvm]``) are merged
    key-by-key so a project setting overrides only that specific key.
    """
    merged = dict(global_backend)
    for key, value in project_backend.items():
        if isinstance(value, dict):
            base = merged.get(key)
            if base is not None and not isinstance(base, dict):
                raise ValueError(f"[backend.{key}] must be a table")
            merged[key] = {**(base or {}), **value}
        else:
            if key == "type" and key in merged and merged[key] != value:
                warn(f"Project backend type {value!r} overrides global {merged[key]!r}")
            merged[key] = value
    return merged


def load_config(project_dir=None, warn=None):
    """Load merged global and project configuration.

    Project config overrides global config. The ``env`` table is merged at the
    key level. The ``container`` table is merged key-by-key, but ``mounts`` are
    merged by ``target``, ``ports`` by host port, ``known_hosts`` by hostname,
    and ``devices`` by host device path so that project values override
    global values without discarding unrelated entries.
    """
    if warn is None:
        warn = warnings.warn

    config = _load(CONFIG_GLOBAL_PATH)
    project_path = pathlib.Path(project_dir or ".").resolve() / CONFIG_PROJECT_PATH
    project_config = _load(project_path)

    if "env" in project_config:
        config.setdefault("env", {}).update(project_config["env"])

    if "scripts" in project_config:
        config.setdefault("scripts", {}).update(project_config["scripts"])

    for key in list(config.get("scripts", {}).keys()):
        if key not in KNOWN_HOOKS:
            warn(f"Unknown script hook {key!r} ignored")
            del config["scripts"][key]
        elif not isinstance(config["scripts"][key], str):
            raise ValueError(f"Script value for {key!r} must be a string")

    global_container = config.get("container", {})
    project_container = project_config.get("container", {})

    # Merge simple container keys directly.
    for key, value in project_container.items():
        if key not in ("mounts", "ports", "known_hosts", "devices"):
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

    if "devices" in global_container or "devices" in project_container:
        global_container["devices"] = _merge_devices(
            global_container.get("devices", []),
            project_container.get("devices", []),
            warn,
        )

    if "known_hosts" in global_container or "known_hosts" in project_container:
        global_container["known_hosts"] = _merge_known_hosts(
            global_container.get("known_hosts", []),
            project_container.get("known_hosts", []),
            warn,
        )

    if global_container:
        config["container"] = global_container

    global_backend = config.get("backend", {})
    project_backend = project_config.get("backend", {})
    if global_backend or project_backend:
        config["backend"] = _merge_backend(global_backend, project_backend, warn)

    return config


def base_image(config):
    """Return the configured base image, or None to use the default."""
    return config.get("container", {}).get("base_image")


def backend_type(config):
    """Return the configured backend type, defaulting to ``podman``."""
    return config.get("backend", {}).get("type", "podman")


def backend_smolvm(config):
    """Return the ``[backend.smolvm]`` settings table (empty if absent)."""
    table = config.get("backend", {}).get("smolvm")
    return dict(table) if isinstance(table, dict) else {}


def backend_smolvm_nested(config):
    """Return whether smol machines should expose nested KVM to the guest.

    ``[backend.smolvm] nested = true`` passes smolvm's ``--nested`` on
    ``machine create``, so the guest gets its own ``/dev/kvm`` and can run
    VMs itself. Requires the host to support nested KVM; smolvm fails the
    start cleanly when it does not.
    """
    return bool(backend_smolvm(config).get("nested", False))


def set_base_image(project_dir, new_image):
    """Update the ``base_image`` entry in the project or global config file.

    The project config is updated if it defines ``base_image``; otherwise the
    global config is updated. Returns the path that was updated, or None if
    neither file defines ``base_image``.
    """
    project_path = pathlib.Path(project_dir or ".").resolve() / CONFIG_PROJECT_PATH
    if _has_base_image(project_path):
        _replace_base_image(project_path, new_image)
        return project_path
    if _has_base_image(CONFIG_GLOBAL_PATH):
        _replace_base_image(CONFIG_GLOBAL_PATH, new_image)
        return CONFIG_GLOBAL_PATH
    return None


def _has_base_image(path):
    """Return True if the TOML file at ``path`` defines a base_image."""
    return "base_image" in _load(path).get("container", {})


def _replace_base_image(path, new_image):
    """Replace the first uncommented base_image line in ``path``."""
    text = path.read_text()
    pattern = re.compile(r"""(?m)^(\s*)base_image\s*=\s*(['"])(.*?)\2""")
    new_text, count = pattern.subn(
        lambda m: f'{m.group(1)}base_image = "{new_image}"',
        text,
        count=1,
    )
    if count:
        path.write_text(new_text)


def container_env(config):
    """Return environment variables from config to set in the container.

    Values must be strings; non-string values are converted to strings.
    Host environment variables are expanded (e.g. ``$SSH_AUTH_SOCK``) so
    that host paths can be passed through without hard-coding them. The
    ``PATH`` key is exempt because ``${PATH}`` is expanded later to the
    image's default PATH.
    """
    env = {}
    for key, value in config.get("env", {}).items():
        value = value if isinstance(value, str) else str(value)
        if key != "PATH":
            value = _expand_host_vars(value)
        env[key] = value
    return env


def container_mounts(config):
    """Return normalized mount dicts for the container runtime.

    Each dict contains ``source``, ``target`` and ``options`` keys. Host
    environment variables (e.g. ``$HOME``) are expanded in ``source`` and
    ``target`` so config files can use portable paths. Use
    :func:`_format_mount` to convert a dict into Podman's ``-v`` syntax.
    """
    mounts = []
    for item in config.get("container", {}).get("mounts", []):
        mount = _normalize_mount(item)
        mount["source"] = _expand_host_vars(mount["source"])
        mount["target"] = _expand_host_vars(mount["target"])
        mounts.append(mount)
    return mounts


def container_ports(config):
    """Return port forwarding strings for the container runtime."""
    return list(config.get("container", {}).get("ports", []))


def container_devices(config):
    """Return host device paths for the container runtime.

    Each entry is a string in Podman's ``--device`` syntax (for example
    ``/dev/kvm``), passed through so host devices are accessible inside the
    container.
    """
    return list(config.get("container", {}).get("devices", []))


def container_build_network(config):
    """Return the network for image builds, or None for the builder default.

    The value is passed to the host builder's ``--network`` flag (for example
    ``host``) so build steps can resolve DNS and reach the network when the
    builder's default container networking cannot (e.g. inside a microVM
    whose container networking is degraded).
    """
    value = config.get("container", {}).get("build_network")
    return value if isinstance(value, str) and value else None


def container_network(config):
    """Return the network for the project container, or None for the default.

    The value is passed to the container runtime's ``--network`` flag (for
    example ``host``) when the runtime's own default networking cannot
    resolve names or reach the network.
    """
    value = config.get("container", {}).get("network")
    return value if isinstance(value, str) and value else None


def container_known_hosts(config):
    """Return known-hosts entries for the container runtime.

    Each entry is a string in ``hostname:ip`` format, passed to Podman's
    ``--add-host`` flag to append entries to the container's ``/etc/hosts``.
    """
    return list(config.get("container", {}).get("known_hosts", []))


def container_podman_socket(config):
    """Return whether to use the host's Podman service via its socket.

    When enabled, the host Podman socket and a matching static Podman client
    are mounted into the container, so builds and containers use the host's
    image and build cache directly.
    """
    return config.get("container", {}).get("podman_socket", False)


def container_scripts(config):
    """Return lifecycle scripts from config.

    Returns a dict mapping hook names (``post_build``, ``post_start``,
    ``entrypoint``) to script names. Project values override global values at
    the key level.
    """
    return dict(config.get("scripts", {}))


def resolve_script(value, project_dir):
    """Resolve a configured script name from project or global config.

    Project scripts take precedence over global scripts. Script values must be
    simple filenames so lifecycle scripts cannot escape either config directory.
    """
    if not isinstance(value, str):
        raise ValueError(f"Script value must be a string, got {type(value).__name__}")

    name = pathlib.Path(value)
    if name.name != value or not value:
        raise ValueError(
            "Script name must be a filename in the project or global config directory"
        )

    project_scripts = pathlib.Path(project_dir).resolve() / CONFIG_PROJECT_PATH.parent
    global_scripts = CONFIG_GLOBAL_PATH.parent
    for directory in (project_scripts, global_scripts):
        path = directory / value
        if path.is_file():
            return path

    raise FileNotFoundError(
        f"Script {value!r} was not found in {project_scripts} or {global_scripts}"
    )
