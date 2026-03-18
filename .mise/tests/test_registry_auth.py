"""Tests for swarm.registry_auth — registry login across nodes."""

from conftest import make_completed
from swarm.registry_auth import login_local, login_node, registry_auth


class TestLoginNode:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.registry_auth.ssh_node",
            lambda h, c, **kw: make_completed(),
        )
        assert login_node("swarm-vm", "reg", "user", "pass") is True

    def test_failure(self, monkeypatch):
        def fail(*args, **kwargs):
            raise Exception("ssh failed")
        monkeypatch.setattr("swarm.registry_auth.ssh_node", fail)
        assert login_node("swarm-vm", "reg", "user", "pass") is False


class TestLoginLocal:
    def test_success(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 0
        assert login_local("reg", "user", "pass") is True

    def test_failure(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 1
        assert login_local("reg", "user", "pass") is False


class TestRegistryAuth:
    def test_all_succeed(self, monkeypatch):
        monkeypatch.setenv("GLOBAL_SWARM_OCI_REGISTRY", "reg.example.com")
        monkeypatch.setenv("REGISTRY_USER", "user")
        monkeypatch.setenv("REGISTRY_PASS", "pass")
        monkeypatch.setattr(
            "swarm.registry_auth.get_swarm_nodes",
            lambda: [{"hostname": "node1"}, {"hostname": "node2"}],
        )
        monkeypatch.setattr("swarm.registry_auth.login_node", lambda *a: True)
        assert registry_auth() == 0

    def test_partial_failure(self, monkeypatch):
        monkeypatch.setenv("GLOBAL_SWARM_OCI_REGISTRY", "reg.example.com")
        monkeypatch.setenv("REGISTRY_USER", "user")
        monkeypatch.setenv("REGISTRY_PASS", "pass")
        monkeypatch.setattr(
            "swarm.registry_auth.get_swarm_nodes",
            lambda: [{"hostname": "node1"}, {"hostname": "node2"}],
        )
        call_count = 0

        def sometimes_fail(*args):
            nonlocal call_count
            call_count += 1
            return call_count != 2

        monkeypatch.setattr("swarm.registry_auth.login_node", sometimes_fail)
        assert registry_auth() == 1

    def test_missing_env(self, monkeypatch):
        monkeypatch.delenv("GLOBAL_SWARM_OCI_REGISTRY", raising=False)
        monkeypatch.delenv("REGISTRY_USER", raising=False)
        monkeypatch.delenv("REGISTRY_PASS", raising=False)
        assert registry_auth() == 1

    def test_with_local(self, monkeypatch):
        monkeypatch.setenv("GLOBAL_SWARM_OCI_REGISTRY", "reg.example.com")
        monkeypatch.setenv("REGISTRY_USER", "user")
        monkeypatch.setenv("REGISTRY_PASS", "pass")
        monkeypatch.setattr("swarm.registry_auth.get_swarm_nodes", lambda: [])
        monkeypatch.setattr("swarm.registry_auth.login_local", lambda *a: True)
        assert registry_auth(local=True) == 0
