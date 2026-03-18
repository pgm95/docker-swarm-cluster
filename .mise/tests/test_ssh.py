"""Tests for swarm._ssh — SSH execution helpers."""

import pytest

from swarm import SSHError
from swarm._ssh import ssh_node


class TestSSHNode:
    def test_basic_command(self, mock_subprocess):
        mock_subprocess.return_value.stdout = "hello"
        mock_subprocess.return_value.returncode = 0
        result = ssh_node("swarm-vm", "hostname")
        cmd = mock_subprocess.call_args[0][0]
        assert "ssh" == cmd[0]
        assert "-n" in cmd
        assert "root@swarm-vm" in cmd
        assert "hostname" in cmd

    def test_custom_user(self, mock_subprocess, monkeypatch):
        monkeypatch.setenv("SWARM_SSH_USER", "deploy")
        mock_subprocess.return_value.returncode = 0
        ssh_node("swarm-vm", "hostname")
        cmd = mock_subprocess.call_args[0][0]
        assert "deploy@swarm-vm" in cmd

    def test_stdin_passthrough(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 0
        ssh_node("swarm-vm", "docker login", stdin_data="password")
        cmd = mock_subprocess.call_args[0][0]
        # -n should NOT be present when stdin_data is provided
        assert "-n" not in cmd
        assert mock_subprocess.call_args[1]["input"] == "password"

    def test_no_stdin_closes_stdin(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 0
        ssh_node("swarm-vm", "hostname")
        cmd = mock_subprocess.call_args[0][0]
        assert "-n" in cmd

    def test_ssh_opts_present(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 0
        ssh_node("swarm-vm", "hostname")
        cmd = mock_subprocess.call_args[0][0]
        assert "StrictHostKeyChecking=no" in " ".join(cmd)

    def test_raises_on_failure(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 255
        mock_subprocess.return_value.stderr = "Connection refused"
        with pytest.raises(SSHError) as exc_info:
            ssh_node("swarm-vm", "hostname")
        assert "swarm-vm" in str(exc_info.value)

    def test_check_false_no_raise(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 1
        mock_subprocess.return_value.stderr = "error"
        result = ssh_node("swarm-vm", "false", check=False)
        assert result.returncode == 1
