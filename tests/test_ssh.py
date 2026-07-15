from unittest.mock import patch, MagicMock

from cauldron import ssh


def test_ensure_keypair_skips_when_keys_exist(tmp_path):
    with patch.object(ssh, "PRIVATE_KEY", tmp_path / "id_ed25519"), \
         patch.object(ssh, "PUBLIC_KEY", tmp_path / "id_ed25519.pub"):
        (tmp_path / "id_ed25519").write_text("key")
        (tmp_path / "id_ed25519.pub").write_text("pubkey")
        with patch("subprocess.run") as mock_run:
            ssh.ensure_keypair()
            mock_run.assert_not_called()


def test_ensure_keypair_generates_when_missing(tmp_path):
    with patch.object(ssh, "SSH_DIR", tmp_path), \
         patch.object(ssh, "PRIVATE_KEY", tmp_path / "id_ed25519"), \
         patch.object(ssh, "PUBLIC_KEY", tmp_path / "id_ed25519.pub"), \
         patch("shutil.which", return_value="/usr/bin/ssh-keygen"):
        result = MagicMock(returncode=0, stderr="")
        with patch("subprocess.run", return_value=result) as mock_run:
            ssh.ensure_keypair()
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "ssh-keygen"
            assert "-t" in args
            assert "ed25519" in args


def test_ensure_keypair_raises_when_keygen_missing(tmp_path):
    with patch.object(ssh, "SSH_DIR", tmp_path), \
         patch.object(ssh, "PRIVATE_KEY", tmp_path / "id_ed25519"), \
         patch.object(ssh, "PUBLIC_KEY", tmp_path / "id_ed25519.pub"), \
         patch("shutil.which", return_value=None):
        try:
            ssh.ensure_keypair()
            assert False, "Should have raised SSHError"
        except ssh.SSHError as exc:
            assert "ssh-keygen" in str(exc)


def test_ensure_keypair_raises_on_failure(tmp_path):
    with patch.object(ssh, "SSH_DIR", tmp_path), \
         patch.object(ssh, "PRIVATE_KEY", tmp_path / "id_ed25519"), \
         patch.object(ssh, "PUBLIC_KEY", tmp_path / "id_ed25519.pub"), \
         patch("shutil.which", return_value="/usr/bin/ssh-keygen"):
        result = MagicMock(returncode=1, stderr="some error")
        with patch("subprocess.run", return_value=result):
            try:
                ssh.ensure_keypair()
                assert False, "Should have raised SSHError"
            except ssh.SSHError as exc:
                assert "some error" in str(exc)


def test_ensure_container_ssh_raises_when_sshd_missing():
    with patch("cauldron.podman.exec_check", return_value=False):
        try:
            ssh.ensure_container_ssh("cauldron-foo")
            assert False, "Should have raised SSHError"
        except ssh.SSHError as exc:
            assert "cauldron up --build" in str(exc)


def test_ensure_container_ssh_installs_keys_and_generates_host_keys():
    pubkey = "ssh-ed25519 AAAA test"
    with patch("cauldron.podman.exec_check", return_value=True), \
         patch("cauldron.podman.exec_with_stdin", return_value=True) as mock_stdin, \
         patch.object(ssh, "read_public_key", return_value=pubkey):
        ssh.ensure_container_ssh("cauldron-foo")
        mock_stdin.assert_called_once()
        stdin_args = mock_stdin.call_args
        assert stdin_args[0][0] == "cauldron-foo"
        assert stdin_args[0][2] == pubkey


def test_ensure_container_ssh_raises_when_authorized_keys_fails():
    with patch("cauldron.podman.exec_check", return_value=True), \
         patch("cauldron.podman.exec_with_stdin", return_value=False), \
         patch.object(ssh, "read_public_key", return_value="pubkey"):
        try:
            ssh.ensure_container_ssh("cauldron-foo")
            assert False, "Should have raised SSHError"
        except ssh.SSHError as exc:
            assert "authorized_keys" in str(exc)


def test_ensure_container_ssh_raises_when_host_keys_fail():
    with patch("cauldron.podman.exec_check") as mock_check, \
         patch("cauldron.podman.exec_with_stdin", return_value=True), \
         patch.object(ssh, "read_public_key", return_value="pubkey"):
        mock_check.side_effect = lambda name, cmd, **kw: (
            True if cmd == ["test", "-f", "/usr/sbin/sshd"] else False
        )
        try:
            ssh.ensure_container_ssh("cauldron-foo")
            assert False, "Should have raised SSHError"
        except ssh.SSHError as exc:
            assert "host keys" in str(exc).lower()


def test_build_remote_uri_format():
    uri = ssh._build_host_block("cauldron-foo")
    assert "Host cauldron-foo" in uri
    assert "User cauldron" in uri
    assert "IdentityFile ~/.config/cauldron/ssh/id_ed25519" in uri
    assert "ProxyCommand podman exec -u 0 -i cauldron-foo" in uri
    assert "/usr/sbin/sshd -i" in uri
    assert "UsePAM=no" in uri
    assert "PasswordAuthentication=no" in uri
    assert "StrictHostKeyChecking no" in uri
    assert "UserKnownHostsFile /dev/null" in uri


def test_ensure_ssh_config_creates_config(tmp_path):
    config_file = tmp_path / "config"
    with patch.object(ssh, "SSH_DIR", tmp_path), \
         patch.object(ssh, "SSH_CONFIG", config_file), \
         patch.object(ssh, "USER_SSH_DIR", tmp_path / "user_ssh"), \
         patch.object(ssh, "USER_SSH_CONFIG", tmp_path / "user_ssh" / "config"), \
         patch.object(ssh, "_ensure_include_in_user_config"):
        ssh.ensure_ssh_config("cauldron-foo")
        content = config_file.read_text()
        assert "# BEGIN cauldron:cauldron-foo" in content
        assert "# END cauldron:cauldron-foo" in content
        assert "Host cauldron-foo" in content


def test_ensure_ssh_config_updates_existing_block(tmp_path):
    config_file = tmp_path / "config"
    config_file.write_text(
        "# BEGIN cauldron:cauldron-foo\nold content\n# END cauldron:cauldron-foo\n"
    )
    with patch.object(ssh, "SSH_DIR", tmp_path), \
         patch.object(ssh, "SSH_CONFIG", config_file), \
         patch.object(ssh, "USER_SSH_DIR", tmp_path / "user_ssh"), \
         patch.object(ssh, "USER_SSH_CONFIG", tmp_path / "user_ssh" / "config"), \
         patch.object(ssh, "_ensure_include_in_user_config"):
        ssh.ensure_ssh_config("cauldron-foo")
        content = config_file.read_text()
        assert "old content" not in content
        assert "Host cauldron-foo" in content
        assert content.count("# BEGIN cauldron:cauldron-foo") == 1


def test_ensure_ssh_config_preserves_other_blocks(tmp_path):
    config_file = tmp_path / "config"
    config_file.write_text(
        "# BEGIN cauldron:cauldron-bar\nHost cauldron-bar\n    User cauldron\n"
        "# END cauldron:cauldron-bar\n"
    )
    with patch.object(ssh, "SSH_DIR", tmp_path), \
         patch.object(ssh, "SSH_CONFIG", config_file), \
         patch.object(ssh, "USER_SSH_DIR", tmp_path / "user_ssh"), \
         patch.object(ssh, "USER_SSH_CONFIG", tmp_path / "user_ssh" / "config"), \
         patch.object(ssh, "_ensure_include_in_user_config"):
        ssh.ensure_ssh_config("cauldron-foo")
        content = config_file.read_text()
        assert "# BEGIN cauldron:cauldron-bar" in content
        assert "# BEGIN cauldron:cauldron-foo" in content
        assert "Host cauldron-bar" in content
        assert "Host cauldron-foo" in content


def test_ensure_include_adds_directive(tmp_path):
    user_ssh_dir = tmp_path / "ssh"
    user_config = user_ssh_dir / "config"
    with patch.object(ssh, "SSH_CONFIG", tmp_path / "cauldron_config"), \
         patch.object(ssh, "USER_SSH_DIR", user_ssh_dir), \
         patch.object(ssh, "USER_SSH_CONFIG", user_config):
        (tmp_path / "cauldron_config").write_text("")
        ssh._ensure_include_in_user_config()
        content = user_config.read_text()
        assert "# BEGIN cauldron" in content
        assert "Include" in content
        assert "# END cauldron" in content


def test_ensure_include_skips_when_already_present(tmp_path):
    user_ssh_dir = tmp_path / "ssh"
    user_config = user_ssh_dir / "config"
    user_config.parent.mkdir(parents=True)
    original = (
        "# BEGIN cauldron\nInclude ~/.config/cauldron/ssh/config\n# END cauldron\n"
    )
    user_config.write_text(original)
    with patch.object(ssh, "SSH_CONFIG", tmp_path / "cauldron_config"), \
         patch.object(ssh, "USER_SSH_DIR", user_ssh_dir), \
         patch.object(ssh, "USER_SSH_CONFIG", user_config):
        ssh._ensure_include_in_user_config()
        assert user_config.read_text() == original


def test_ensure_include_preserves_existing_content(tmp_path):
    user_ssh_dir = tmp_path / "ssh"
    user_config = user_ssh_dir / "config"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("Host *\n    ServerAliveInterval 60\n")
    with patch.object(ssh, "SSH_CONFIG", tmp_path / "cauldron_config"), \
         patch.object(ssh, "USER_SSH_DIR", user_ssh_dir), \
         patch.object(ssh, "USER_SSH_CONFIG", user_config):
        (tmp_path / "cauldron_config").write_text("")
        ssh._ensure_include_in_user_config()
        content = user_config.read_text()
        assert "ServerAliveInterval" in content
        assert "# BEGIN cauldron" in content
