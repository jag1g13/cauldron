"""Recover from Dev Container template images that Podman cannot build from.

Dev Container *templates* (``ghcr.io/devcontainers/templates/...``) are OCI
artifacts whose feature layers use a non-standard media type
(``application/vnd.devcontainers.layer.v1+tar``). Buildah/Podman cannot
convert them to a buildable image manifest, so ``podman build`` fails with
``Unknown media type during manifest conversion``.

The equivalent pre-built images live at ``mcr.microsoft.com/devcontainers/...``
(see https://github.com/devcontainers/images) and use normal Docker/OCI
layers, so they build fine. This module detects that specific failure and
resolves the matching MCR image.
"""

import json
import re
import urllib.request

TEMPLATES_PREFIX = "ghcr.io/devcontainers/templates/"
MCR_PREFIX = "mcr.microsoft.com/devcontainers/"
TEMPLATE_ERROR_SIGNATURE = "application/vnd.devcontainers.layer.v1+tar"


def is_template_image(ref):
    """Return True if ``ref`` points at a Dev Container template OCI artifact."""
    return "/devcontainers/templates/" in ref


def is_template_manifest_error(stderr):
    """Return True if ``stderr`` shows the template media-type conversion error."""
    return bool(stderr) and TEMPLATE_ERROR_SIGNATURE in stderr


def template_name(ref):
    """Return the template name (e.g. ``typescript-node``) part of ``ref``."""
    name, _ = _parse_template_ref(ref)
    return name


def resolve_mcr_image(template_ref):
    """Return the matching ``mcr.microsoft.com/devcontainers`` image, or None.

    The template tag (e.g. ``5.0.0``) maps to an MCR tag of the form
    ``<major>.<minor>-<node>`` (e.g. ``5.0-22``); the highest available node
    version is chosen. Falls back to ``<major>.<minor>-<distro>`` or
    ``latest``. Returns None if the tags cannot be queried or no suitable tag
    exists.
    """
    name, tag = _parse_template_ref(template_ref)
    if not name:
        return None
    tags = _fetch_tags(f"devcontainers/{name}")
    if tags is None:
        return None
    chosen = _pick_tag(tags, tag)
    if not chosen:
        return None
    return f"{MCR_PREFIX}{name}:{chosen}"


def _parse_template_ref(ref):
    """Split a template ref into ``(name, tag)``. ``tag`` may be None."""
    parts = ref.split("/")
    if "templates" in parts:
        idx = parts.index("templates")
        tail = parts[idx + 1] if idx + 1 < len(parts) else ""
    else:
        tail = parts[-1]
    # Drop an optional digest (``name@sha256:...``).
    tail = tail.split("@", 1)[0]
    if ":" in tail:
        name, _, tag = tail.partition(":")
    else:
        name, tag = tail, None
    return name or None, tag


def _fetch_tags(repo, timeout=10):
    """Fetch the tag list for an MCR repo, or None on failure."""
    url = f"https://mcr.microsoft.com/v2/{repo}/tags/list"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    tags = payload.get("tags")
    return tags or None


def _pick_tag(tags, template_tag):
    """Choose the best MCR tag for a given template tag."""
    if not template_tag:
        return "latest" if "latest" in tags else None
    m = re.match(r"^(\d+)\.(\d+)", template_tag)
    if not m:
        if template_tag in tags:
            return template_tag
        return "latest" if "latest" in tags else None
    prefix = f"{m.group(1)}.{m.group(2)}"
    node_tags = []
    for t in tags:
        mm = re.match(rf"^{re.escape(prefix)}-(\d+)$", t)
        if mm:
            node_tags.append((int(mm.group(1)), t))
    if node_tags:
        node_tags.sort()
        return node_tags[-1][1]
    for distro in ("trixie", "bookworm", "bullseye"):
        candidate = f"{prefix}-{distro}"
        if candidate in tags:
            return candidate
    if "latest" in tags:
        return "latest"
    return None
