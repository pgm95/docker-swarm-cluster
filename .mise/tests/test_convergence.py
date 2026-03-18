"""Tests for swarm.convergence — deploy convergence polling."""

from swarm.convergence import (
    _all_replicas_healthy,
    _parse_replicas,
    check_replica_health,
    is_task_complete,
    wait_for_convergence,
)


class TestParseReplicas:
    def test_normal(self):
        assert _parse_replicas("1/1") == (1, 1)

    def test_zero(self):
        assert _parse_replicas("0/1") == (0, 1)

    def test_global(self):
        assert _parse_replicas("3/3") == (3, 3)


class TestIsTaskComplete:
    def test_complete(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.convergence.service_ps",
            lambda *a, **kw: ["Complete 2 minutes ago"],
        )
        assert is_task_complete("mystack_init-db") is True

    def test_failed(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.convergence.service_ps",
            lambda *a, **kw: ["Failed 1 minute ago"],
        )
        assert is_task_complete("mystack_init-db") is False

    def test_no_shutdown_tasks(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.convergence.service_ps",
            lambda *a, **kw: [],
        )
        assert is_task_complete("mystack_init-db") is False

    def test_complete_with_timestamp(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.convergence.service_ps",
            lambda *a, **kw: ["Complete 30 seconds ago"],
        )
        assert is_task_complete("mystack_init-db") is True


class TestAllReplicasHealthy:
    def test_all_healthy(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [("svc_web", "1/1"), ("svc_db", "1/1")],
        )
        assert _all_replicas_healthy("mystack") is True

    def test_unhealthy(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [("svc_web", "0/1"), ("svc_db", "1/1")],
        )
        monkeypatch.setattr("swarm.convergence.is_task_complete", lambda s: False)
        assert _all_replicas_healthy("mystack") is False

    def test_init_sidecar_complete(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [("svc_web", "1/1"), ("svc_init-db", "0/1")],
        )
        monkeypatch.setattr("swarm.convergence.is_task_complete", lambda s: True)
        assert _all_replicas_healthy("mystack") is True


class TestCheckReplicaHealth:
    def test_all_healthy(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [("svc_web", "1/1")],
        )
        assert check_replica_health("mystack") == []

    def test_unhealthy(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [("svc_web", "0/1")],
        )
        monkeypatch.setattr("swarm.convergence.is_task_complete", lambda s: False)
        monkeypatch.setattr(
            "swarm.convergence.stack_ps",
            lambda *a, **kw: [],
        )
        result = check_replica_health("mystack")
        assert len(result) == 1
        assert result[0]["name"] == "svc_web"
        assert result[0]["replicas"] == "0/1"

    def test_init_sidecar_exempt(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [("svc_init-db", "0/1")],
        )
        monkeypatch.setattr("swarm.convergence.is_task_complete", lambda s: True)
        assert check_replica_health("mystack") == []


class TestWaitForConvergence:
    def test_converges_immediately(self, monkeypatch):
        monkeypatch.setattr("swarm.convergence.stack_ps", lambda *a, **kw: [["Running 10s"]])
        monkeypatch.setattr("swarm.convergence._all_replicas_healthy", lambda s: True)
        assert wait_for_convergence("mystack", timeout=5, interval=0) is True

    def test_converges_after_pending(self, monkeypatch):
        call_count = 0

        def fake_stack_ps(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return [["Pending"]]
            return [["Running 10s"]]

        monkeypatch.setattr("swarm.convergence.stack_ps", fake_stack_ps)
        monkeypatch.setattr("swarm.convergence._all_replicas_healthy", lambda s: True)
        monkeypatch.setattr("swarm.convergence.time.sleep", lambda s: None)
        assert wait_for_convergence("mystack", timeout=60, interval=1) is True

    def test_timeout(self, monkeypatch):
        monkeypatch.setattr("swarm.convergence.stack_ps", lambda *a, **kw: [["Pending"]])
        monkeypatch.setattr("swarm.convergence.time.sleep", lambda s: None)
        # Use a very short timeout — time.monotonic still advances
        assert wait_for_convergence("mystack", timeout=0, interval=0) is False
