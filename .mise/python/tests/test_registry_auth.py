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
    def test_success(self, mock_docker):
        mock_docker.set_response("login", returncode=0)
        assert login_local("reg", "user", "pass") is True

    def test_failure(self, mock_docker):
        mock_docker.set_response("login", returncode=1, stderr="bad creds")
        assert login_local("reg", "user", "pass") is False

    def test_password_piped_via_stdin(self, mock_docker):
        login_local("reg", "user", "secretpass")
        # The password should arrive via the input= kwarg, not in argv
        assert mock_docker.inputs[0] == "secretpass"
        assert "secretpass" not in mock_docker.calls[0]


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
        monkeypatch.setattr("swarm.registry_auth.get_swarm_nodes", list)
        monkeypatch.setattr("swarm.registry_auth.login_local", lambda *a: True)
        assert registry_auth(local=True) == 0

    def test_parallel_preserves_node_mapping(self, monkeypatch):
        """Concurrent login_node calls produce correct per-target results."""
        import time
        monkeypatch.setenv("GLOBAL_SWARM_OCI_REGISTRY", "reg.example.com")
        monkeypatch.setenv("REGISTRY_USER", "user")
        monkeypatch.setenv("REGISTRY_PASS", "pass")
        monkeypatch.setattr(
            "swarm.registry_auth.get_swarm_nodes",
            lambda: [{"hostname": "alpha"}, {"hostname": "bravo"}, {"hostname": "charlie"}],
        )
        # bravo fails, others succeed; introduce out-of-order completion
        def login(host, *a):
            delays = {"alpha": 0.02, "bravo": 0.005, "charlie": 0.01}
            time.sleep(delays.get(host, 0))
            return host != "bravo"
        monkeypatch.setattr("swarm.registry_auth.login_node", login)
        # Should return 1 because one target failed
        assert registry_auth() == 1


class TestMainCli:
    """The CLI uses argparse `--local` directly (no env-var coupling to mise)."""

    def test_no_flag_skips_local(self, monkeypatch):
        from swarm.registry_auth import main
        monkeypatch.setenv("GLOBAL_SWARM_OCI_REGISTRY", "reg")
        monkeypatch.setenv("REGISTRY_USER", "u")
        monkeypatch.setenv("REGISTRY_PASS", "p")
        monkeypatch.setattr("swarm.registry_auth.get_swarm_nodes", list)
        local_called = []
        monkeypatch.setattr(
            "swarm.registry_auth.login_local",
            lambda *a: local_called.append(a) or True,
        )
        # Empty argv (no --local)
        monkeypatch.setattr("sys.argv", ["swarm.registry_auth"])
        assert main() == 0
        assert local_called == []  # local skipped without flag

    def test_local_flag_invokes_login_local(self, monkeypatch):
        from swarm.registry_auth import main
        monkeypatch.setenv("GLOBAL_SWARM_OCI_REGISTRY", "reg")
        monkeypatch.setenv("REGISTRY_USER", "u")
        monkeypatch.setenv("REGISTRY_PASS", "p")
        monkeypatch.setattr("swarm.registry_auth.get_swarm_nodes", list)
        local_called = []
        monkeypatch.setattr(
            "swarm.registry_auth.login_local",
            lambda *a: local_called.append(a) or True,
        )
        monkeypatch.setattr("sys.argv", ["swarm.registry_auth", "--local"])
        assert main() == 0
        assert len(local_called) == 1
