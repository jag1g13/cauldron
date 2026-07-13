from unittest.mock import patch

from cauldron import podman


def test_run_returns_completed_process_on_success():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        result = podman._run(["version"])
        assert result.returncode == 0
        mock_run.assert_called_once_with(
            ["podman", "version"], capture_output=True, text=True
        )


def test_run_returns_nonzero_when_podman_not_found():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        result = podman._run(["version"])
        assert result.returncode == 1


def test_version_returns_true_when_podman_works():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert podman.version() is True
        mock_run.assert_called_once_with(["version"])


def test_version_returns_false_when_podman_fails():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 1
        assert podman.version() is False


def test_ensure_base_image_returns_true_if_image_exists():
    with patch("cauldron.podman.image_exists", return_value=True) as mock_exists:
        assert podman.ensure_base_image() is True
        mock_exists.assert_called_once_with("cauldron-base:latest")


def test_ensure_base_image_pulls_and_tags_when_missing():
    with patch("cauldron.podman.image_exists", return_value=False):
        with patch("cauldron.podman.pull", return_value=True) as mock_pull:
            with patch("cauldron.podman.tag", return_value=True) as mock_tag:
                assert podman.ensure_base_image() is True
                mock_pull.assert_called_once_with(podman.BASE_IMAGE)
                mock_tag.assert_called_once_with(
                    podman.BASE_IMAGE, podman.LOCAL_BASE_TAG
                )


def test_ensure_base_image_fails_when_pull_fails():
    with patch("cauldron.podman.image_exists", return_value=False):
        with patch("cauldron.podman.pull", return_value=False):
            assert podman.ensure_base_image() is False


def test_run_test_container_returns_true_on_success():
    with patch("cauldron.podman._run") as mock_run:
        mock_run.return_value.returncode = 0
        assert podman.run_test_container() is True
        mock_run.assert_called_once_with(
            ["run", "--rm", "cauldron-base:latest", "echo", "cauldron-check-ok"]
        )
