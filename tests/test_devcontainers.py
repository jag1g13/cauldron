from unittest.mock import patch

from cauldron import devcontainers


def test_is_template_image_detects_template_ref():
    assert devcontainers.is_template_image(
        "ghcr.io/devcontainers/templates/typescript-node:5.0.0"
    )


def test_is_template_image_rejects_prebuilt_image():
    assert not devcontainers.is_template_image(
        "mcr.microsoft.com/devcontainers/typescript-node:5.0-24"
    )
    assert not devcontainers.is_template_image("docker.io/library/debian:trixie-slim")


def test_is_template_manifest_error_matches_signature():
    stderr = (
        "Error: error creating build container: Unknown media type during "
        'manifest conversion: "application/vnd.devcontainers.layer.v1+tar"'
    )
    assert devcontainers.is_template_manifest_error(stderr)


def test_is_template_manifest_error_rejects_other_errors():
    assert not devcontainers.is_template_manifest_error("some other build error")
    assert not devcontainers.is_template_manifest_error("")
    assert not devcontainers.is_template_manifest_error(None)


def test_template_name_extracts_name():
    assert (
        devcontainers.template_name(
            "ghcr.io/devcontainers/templates/typescript-node:5.0.0"
        )
        == "typescript-node"
    )


def test_template_name_without_tag():
    assert devcontainers.template_name("ghcr.io/devcontainers/templates/go") == "go"


def test_template_name_with_digest():
    assert (
        devcontainers.template_name(
            "ghcr.io/devcontainers/templates/python@sha256:abc123"
        )
        == "python"
    )


def test_pick_tag_prefers_highest_node_version():
    tags = ["5.0-22", "5.0-24", "5.0-bookworm", "5.0-trixie", "latest"]
    assert devcontainers._pick_tag(tags, "5.0.0") == "5.0-24"


def test_pick_tag_falls_back_to_distro():
    tags = ["5.0-bookworm", "5.0-trixie", "latest"]
    assert devcontainers._pick_tag(tags, "5.0.0") == "5.0-trixie"


def test_pick_tag_falls_back_to_latest():
    assert devcontainers._pick_tag(["latest"], "5.0.0") == "latest"


def test_pick_tag_returns_none_when_nothing_matches():
    assert devcontainers._pick_tag(["unrelated"], "5.0.0") is None


def test_pick_tag_handles_non_semver_tag():
    assert devcontainers._pick_tag(["latest", "foo"], "foo") == "foo"
    assert devcontainers._pick_tag(["latest"], "missing-tag") == "latest"


def test_pick_tag_without_template_tag_uses_latest():
    assert devcontainers._pick_tag(["5.0-22", "latest"], None) == "latest"


def test_resolve_mcr_image_returns_verified_image():
    tags = ["5.0-22", "5.0-24", "5.0-bookworm", "latest"]
    with patch("cauldron.devcontainers._fetch_tags", return_value=tags):
        result = devcontainers.resolve_mcr_image(
            "ghcr.io/devcontainers/templates/typescript-node:5.0.0"
        )
    assert result == "mcr.microsoft.com/devcontainers/typescript-node:5.0-24"


def test_resolve_mcr_image_returns_none_when_registry_unreachable():
    with patch("cauldron.devcontainers._fetch_tags", return_value=None):
        result = devcontainers.resolve_mcr_image(
            "ghcr.io/devcontainers/templates/typescript-node:5.0.0"
        )
    assert result is None


def test_resolve_mcr_image_uses_latest_when_tag_missing():
    with patch("cauldron.devcontainers._fetch_tags", return_value=["5.0-24", "latest"]):
        result = devcontainers.resolve_mcr_image(
            "ghcr.io/devcontainers/templates/typescript-node"
        )
    assert result == "mcr.microsoft.com/devcontainers/typescript-node:latest"
