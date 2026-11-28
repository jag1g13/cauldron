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
from unittest.mock import MagicMock

import pytest

from cauldron import backend, config, podman, project, ssh

FAKE_PODMAN = pathlib.Path(__file__).parent / "fake_podman.py"
FAKE_SMOLVM = pathlib.Path(__file__).parent / "fake_smolvm.py"
BUSYBOX_IMAGE = "docker.io/library/busybox:1.37.0"


class FakeRuntime:
    """Handle for a recording runtime executable used by argument-level tests."""

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


@pytest.fixture(autouse=True, scope="session")
def ensure_basetemp(request):
    """Create the configured --basetemp path before the first tmp_path use.

    ``--basetemp=tmp/pytest`` sits under the gitignored ``tmp/`` directory,
    which does not exist on a fresh checkout or CI runner, and pytest can
    only create the final component itself.
    """
    basetemp = request.config.option.basetemp
    if basetemp:
        pathlib.Path(basetemp).mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def reset_verbose():
    """Keep the module-level verbose flag from leaking between tests."""
    podman.set_verbose(False)
    yield
    podman.set_verbose(False)


@pytest.fixture(autouse=True)
def reset_backend():
    """Re-resolve the runtime backend for each test (no caching across tests)."""
    backend.reset()
    yield
    backend.reset()


@pytest.fixture(autouse=True)
def isolate_user_config(tmp_path, monkeypatch):
    """Stop ambient configs from leaking into tests.

    Runs from a fresh empty directory (so the repository's own project
    config — which selects the runtime backend — is never read), and points
    the global config and Dockerfile paths at test-local files.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "CONFIG_GLOBAL_PATH", tmp_path / "global.toml")
    monkeypatch.setattr(
        project, "DOCKERFILE_GLOBAL_PATH", tmp_path / "global.Dockerfile"
    )


@pytest.fixture(autouse=True)
def stub_remote_ssh(monkeypatch):
    """Stop ``up``/``code`` from performing real Remote-SSH setup in tests.

    The wrapper is replaced with a no-op so no key generation, in-container
    exec, or SSH config writes happen. Tests that exercise the setup patch
    ``ssh.ensure_remote_ssh`` (or the inner functions) explicitly.
    """
    monkeypatch.setattr(ssh, "ensure_remote_ssh", MagicMock())


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
    # The fake is a controlled, non-remote podman: clear the host-socket marker
    # so an ambient nested-cauldron environment does not switch commands into the
    # nested code path. Nested tests set running_in_remote explicitly.
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    return FakeRuntime(log, responses)


@pytest.fixture
def fake_smolvm(tmp_path, monkeypatch):
    """Point smolvm at the recording fake and return its handle."""
    log = tmp_path / "smolvm.log"
    responses = tmp_path / "smolvm-responses.json"
    state = tmp_path / "smolvm.state"
    binary = tmp_path / "smolvm"
    binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_SMOLVM}" "$@"\n')
    binary.chmod(0o755)
    monkeypatch.setenv("CAULDRON_SMOLVM_PATH", str(binary))
    monkeypatch.setenv("FAKE_SMOLVM_LOG", str(log))
    monkeypatch.setenv("FAKE_SMOLVM_RESPONSES", str(responses))
    monkeypatch.setenv("FAKE_SMOLVM_STATE", str(state))
    # The built project image is a docker-save tar; keep it in a test-local dir.
    monkeypatch.setenv("CAULDRON_SMOLTAR_DIR", str(tmp_path / "smoltar"))
    return FakeRuntime(log, responses)
