"""Tests for swarm.status — cluster status display."""

from conftest import SAMPLE_NODES
from swarm.status import get_node_status, status


class TestGetNodeStatus:
    def test_parses_nodes(self, monkeypatch):
        monkeypatch.setattr("swarm.status.inspect_nodes", lambda: SAMPLE_NODES)
        nodes = get_node_status()
        assert len(nodes) == 3
        vm = next(n for n in nodes if n["hostname"] == "swarm-vm")
        assert vm["role"] == "Leader"
        assert "ready" in vm["status"]
        assert "29.2.1" == vm["engine"]
        assert "location=onprem" in vm["labels"]

    def test_worker_role(self, monkeypatch):
        monkeypatch.setattr("swarm.status.inspect_nodes", lambda: SAMPLE_NODES)
        nodes = get_node_status()
        vps = next(n for n in nodes if n["hostname"] == "nerd1")
        assert vps["role"] == "Worker"


class TestStatus:
    def _patch_status(self, monkeypatch, services, running_tasks, shutdown_tasks=None):
        """Helper to set up mocks for the batch-query status function."""
        monkeypatch.setattr("swarm.status.inspect_nodes", lambda: SAMPLE_NODES)
        monkeypatch.setattr("swarm.status.service_ls", lambda: services)
        monkeypatch.setattr("swarm.status.find_stacks", lambda ns: [f"{ns}/mystack"])
        monkeypatch.setattr("swarm.status.stack_name", lambda d: d.rsplit("/", 1)[-1])

        def mock_ps_multi(names, format_str="", filters=None):
            if filters and "desired-state=shutdown" in filters:
                return shutdown_tasks or []
            return running_tasks

        monkeypatch.setattr("swarm.status.service_ps_multi", mock_ps_multi)

    def test_healthy(self, monkeypatch, capsys):
        self._patch_status(
            monkeypatch,
            services=[("mystack_web", "1/1")],
            running_tasks=[["mystack_web.1.abc123", "swarm-vm"]],
        )
        rc = status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "healthy" in out
        assert "web(1/1)" in out
        assert "swarm-vm" in out

    def test_unhealthy(self, monkeypatch, capsys):
        self._patch_status(
            monkeypatch,
            services=[("mystack_web", "0/1")],
            running_tasks=[],
            shutdown_tasks=[["mystack_web.1.abc123", "Failed 5 seconds ago"]],
        )
        rc = status()
        assert rc == 1
        out = capsys.readouterr().out
        assert "UNHEALTHY" in out

    def test_init_sidecar_complete(self, monkeypatch, capsys):
        self._patch_status(
            monkeypatch,
            services=[("mystack_web", "1/1"), ("mystack_init-db", "0/1")],
            running_tasks=[["mystack_web.1.abc123", "swarm-vm"]],
            shutdown_tasks=[["mystack_init-db.1.abc123", "Complete 10 seconds ago"]],
        )
        rc = status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "healthy" in out
        assert "init-db(0/1)" in out

    def test_not_deployed(self, monkeypatch, capsys):
        self._patch_status(
            monkeypatch,
            services=[],
            running_tasks=[],
        )
        rc = status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Not deployed:" in out
        assert "mystack" in out
