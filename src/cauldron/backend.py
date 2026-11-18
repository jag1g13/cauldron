"""Select and expose the active runtime backend.

Cauldron drives its container/VM runtime through this module rather than
importing a specific runtime directly. The active backend is resolved lazily
from the project configuration and cached for the process, so call sites reach
it with :func:`active`.

``podman`` is the default and existing backend. ``smolvm`` runs each project in
a smol machine.
"""

import cauldron.podman as podman

from cauldron import config

KNOWN_BACKENDS = ("podman", "smolvm")

_current = None


def _lookup(backend_type):
    if backend_type == "podman":
        return podman
    if backend_type == "smolvm":
        try:
            import cauldron.smolvm as smolvm
        except ModuleNotFoundError as exc:
            raise ValueError(
                "The smolvm backend is not available in this version."
            ) from exc
        return smolvm
    raise ValueError(
        f"Unknown backend type {backend_type!r} "
        f"(expected one of: {', '.join(KNOWN_BACKENDS)})"
    )


def active():
    """Return the active backend module, resolving it from config on first use.

    Defaults to the configured backend type (``podman`` when unset). The result
    is cached for the process; call :func:`reset` to force re-resolution (used
    between tests).
    """
    global _current
    if _current is None:
        _current = _lookup(config.backend_type(config.load_config()))
    return _current


def reset():
    """Clear the cached backend so the next :func:`active` re-resolves."""
    global _current
    _current = None
