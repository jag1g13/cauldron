"""Shared pytest fixtures for the Cauldron test suite.

Integration tests run against whichever Podman the environment provides through
the usual ``CAULDRON_PODMAN_PATH`` / ``podman`` resolution, so they work both
with a locally installed Podman and with the static ``podman-remote`` client
used inside a cauldron. Tests skip cleanly when no service is reachable.
"""

import json
import pathlib
import sys
import uuid

import pytest

from cauldron import config, podman, project

FAKE_PODMAN = pathlib.Path(__file__).parent / "fake_podman.py"
BUSYBOX_IMAGE = "docker.io/library/busybox:1.37.0"


class FakePodman:
    """Handle for the recording podman executable used by argument-level tests."""

    def __init__(self, log, responses):
        self.log = pathlib.Path(log)
        self.responses = pathlib.Path(responses)

    def calls(self):
        """Return the argv of every command the fake has run, in order."""
        if not self.log.exists():
            return []
        return [
            json.loads(line)["argv"]
            for line in self.log.read_text().splitlines()
            if line
        ]

    def respond(self, argv, stdout="", stderr="", returncode=0, times=None):
        """Register a response rule for the given argv prefix.

        ``times`` limits the rule to that many invocations, after which later
        matching rules are used instead.
        """
        rules = []
        if self.responses.exists():
            rules = json.loads(self.responses.read_text())
        rule = {
            "argv": list(argv),
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode,
        }
        if times is not None:
            rule["times"] = times
        rules.append(rule)
        self.responses.write_text(json.dumps(rules))


def _podman_reachable():
    try:
        return podman.version()
    except Exception:
        return False


@pytest.fixture(autouse=True)
def reset_verbose():
    """Keep the module-level verbose flag from leaking between tests."""
    podman.set_verbose(False)
    yield
    podman.set_verbose(False)


@pytest.fixture(autouse=True)
def isolate_user_config(tmp_path, monkeypatch):
    """Stop the ambient user config and Dockerfile from affecting tests."""
    monkeypatch.setattr(config, "CONFIG_GLOBAL_PATH", tmp_path / "global.toml")
    monkeypatch.setattr(
        project, "DOCKERFILE_GLOBAL_PATH", tmp_path / "global.Dockerfile"
    )


@pytest.fixture(scope="session")
def podman_service():
    """Skip integration tests when no Podman service is reachable."""
    if not _podman_reachable():
        pytest.skip("no reachable Podman service")
    return True


@pytest.fixture(scope="session")
def base_image(podman_service):
    """The project's own base image, pulled once per session."""
    if not podman.ensure_base_image():
        pytest.skip(f"could not obtain base image {podman.BASE_IMAGE}")
    return podman.BASE_IMAGE


@pytest.fixture(scope="session")
def fast_image(podman_service):
    """A small base image for fast non-build tests, falling back to Debian."""
    if podman.image_exists(BUSYBOX_IMAGE) or podman.pull(BUSYBOX_IMAGE):
        return BUSYBOX_IMAGE
    if podman.ensure_base_image():
        return podman.BASE_IMAGE
    pytest.skip("could not obtain a base image")


class Scratch:
    """Track and clean up test containers and images."""

    def __init__(self, prefix):
        self.prefix = prefix
        self.containers = []
        self.images = []

    def container(self, suffix=""):
        name = f"{self.prefix}-{uuid.uuid4().hex[:8]}"
        if suffix:
            name = f"{name}-{suffix}"
        self.containers.append(name)
        return name

    def image(self, suffix=""):
        name = f"{self.prefix}-{uuid.uuid4().hex[:8]}"
        if suffix:
            name = f"{name}-{suffix}"
        self.images.append(name)
        return name


@pytest.fixture
def scratch():
    """Provide unique names with automatic container/image cleanup."""
    helper = Scratch("cauldron-test")
    yield helper
    for name in reversed(helper.containers):
        podman.remove_container(name, force=True)
    for image in reversed(helper.images):
        podman._run(["rmi", "-f", image])


@pytest.fixture
def fake_podman(tmp_path, monkeypatch):
    """Point podman at the recording fake and return its handle.

    A small shell wrapper is used so the fake runs with the same interpreter as
    the test suite, independent of what is available on ``PATH``.
    """
    log = tmp_path / "podman.log"
    responses = tmp_path / "responses.json"
    state = tmp_path / "podman.state"
    binary = tmp_path / "podman"
    binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_PODMAN}" "$@"\n')
    binary.chmod(0o755)
    monkeypatch.setenv("CAULDRON_PODMAN_PATH", str(binary))
    monkeypatch.setenv("FAKE_PODMAN_LOG", str(log))
    monkeypatch.setenv("FAKE_PODMAN_RESPONSES", str(responses))
    monkeypatch.setenv("FAKE_PODMAN_STATE", str(state))
    return FakePodman(log, responses)
