"""Tests for swarm.status — cluster status display."""

from conftest import SAMPLE_NODES
from swarm.status import get_node_status, get_stack_health


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


class TestGetStackHealth:
    def test_healthy(self, monkeypatch):
        monkeypatch.setattr("swarm.status.stack_list", lambda: ["mystack"])
        monkeypatch.setattr("swarm.status.stack_services", lambda s: [("mystack_web", "1/1")])
        monkeypatch.setattr("swarm.status.stack_ps", lambda *a, **kw: [["web.1", "swarm-vm"]])
        result = get_stack_health("mystack")
        assert result["status"] == "healthy"
        assert result["services"][0]["name"] == "web"
        assert "swarm-vm" in result["nodes"]

    def test_unhealthy(self, monkeypatch):
        monkeypatch.setattr("swarm.status.stack_list", lambda: ["mystack"])
        monkeypatch.setattr("swarm.status.stack_services", lambda s: [("mystack_web", "0/1")])
        monkeypatch.setattr("swarm.status.is_task_complete", lambda s: False)
        monkeypatch.setattr("swarm.status.stack_ps", lambda *a, **kw: [["web.1", "task failed"]])
        result = get_stack_health("mystack")
        assert result["status"] == "UNHEALTHY"

    def test_init_sidecar_complete(self, monkeypatch):
        monkeypatch.setattr("swarm.status.stack_list", lambda: ["mystack"])
        monkeypatch.setattr("swarm.status.stack_services", lambda s: [
            ("mystack_web", "1/1"),
            ("mystack_init-db", "0/1"),
        ])
        monkeypatch.setattr("swarm.status.is_task_complete", lambda s: True)
        monkeypatch.setattr("swarm.status.stack_ps", lambda *a, **kw: [["web.1", "swarm-vm"]])
        result = get_stack_health("mystack")
        assert result["status"] == "healthy"

    def test_not_deployed(self, monkeypatch):
        monkeypatch.setattr("swarm.status.stack_list", lambda: [])
        result = get_stack_health("mystack")
        assert result["status"] == "not deployed"
