from unittest.mock import patch

from cauldron import vscode


def test_code_available_when_code_on_path():
    with patch("shutil.which", return_value="/usr/bin/code"):
        assert vscode.code_available() is True


def test_code_unavailable_when_not_on_path():
    with patch("shutil.which", return_value=None):
        assert vscode.code_available() is False


def test_build_remote_uri_encodes_container_name():
    uri = vscode.build_remote_uri("cauldron-foo", "/home/deck/projects/foo")
    assert uri.startswith("vscode-remote://attached-container+")
    assert "cauldron-foo" in uri
    assert "/home/deck/projects/foo" in uri


def test_build_remote_uri_encodes_special_characters():
    uri = vscode.build_remote_uri("cauldron_foo", "/home/deck/projects/foo bar")
    assert "foo%20bar" in uri


def test_open_in_code_runs_code_cli():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        assert vscode.open_in_code("cauldron-foo", "/home/deck/projects/foo") == 0
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "code"
        assert args[1] == "--folder-uri"


def test_open_in_code_returns_error_when_code_missing():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        assert vscode.open_in_code("cauldron-foo", "/home/deck/projects/foo") == 1
