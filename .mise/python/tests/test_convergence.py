"""Tests for swarm.convergence — deploy convergence polling."""

from swarm._docker import parse_replicas
from swarm.convergence import (
    _all_replicas_healthy,
    _collect_unhealthy,
    _task_errors_by_service,
    is_task_complete,
    verify,
)


class TestParseReplicas:
    def test_normal(self):
        assert parse_replicas("1/1") == (1, 1)

    def test_zero(self):
        assert parse_replicas("0/1") == (0, 1)

    def test_global(self):
        assert parse_replicas("3/3") == (3, 3)

    def test_transient_na(self):
        """Swarm can transiently emit 'N/A' for global services during reconfigure."""
        assert parse_replicas("N/A") is None

    def test_malformed(self):
        assert parse_replicas("") is None
        assert parse_replicas("1/2/3") is None
        assert parse_replicas("garbage") is None


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


class TestCollectUnhealthy:
    def test_all_healthy(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [("svc_web", "1/1")],
        )
        assert _collect_unhealthy("mystack") == []

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
        result = _collect_unhealthy("mystack")
        assert len(result) == 1
        assert result[0]["name"] == "svc_web"
        assert result[0]["replicas"] == "0/1"

    def test_init_sidecar_exempt(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [("svc_init-db", "0/1")],
        )
        monkeypatch.setattr("swarm.convergence.is_task_complete", lambda s: True)
        assert _collect_unhealthy("mystack") == []


class TestVerify:
    def test_converged_and_healthy(self, monkeypatch):
        monkeypatch.setattr("swarm.convergence.stack_ps", lambda *a, **kw: [["Running 10s"]])
        monkeypatch.setattr("swarm.convergence._all_replicas_healthy", lambda s: True)
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [("svc_web", "1/1")],
        )
        converged, unhealthy = verify("mystack", timeout=5, interval=0)
        assert converged is True
        assert unhealthy == []

    def test_converged_but_one_unhealthy(self, monkeypatch):
        # Phase 1 considers the stack converged (initial poll passes), then
        # Phase 2 finds one service short on replicas.
        monkeypatch.setattr("swarm.convergence.stack_ps", lambda *a, **kw: [["Running 10s"]])
        monkeypatch.setattr("swarm.convergence._all_replicas_healthy", lambda s: True)
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [("svc_web", "0/1")],
        )
        monkeypatch.setattr("swarm.convergence.is_task_complete", lambda s: False)
        converged, unhealthy = verify("mystack", timeout=5, interval=0)
        assert converged is True
        assert len(unhealthy) == 1
        assert unhealthy[0]["name"] == "svc_web"

    def test_phase1_timeout_still_returns_phase2(self, monkeypatch):
        monkeypatch.setattr("swarm.convergence.stack_ps", lambda *a, **kw: [["Pending"]])
        monkeypatch.setattr("swarm.convergence.time.sleep", lambda s: None)
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [("svc_web", "0/1")],
        )
        monkeypatch.setattr("swarm.convergence.is_task_complete", lambda s: False)
        converged, unhealthy = verify("mystack", timeout=0, interval=0)
        assert converged is False
        # Phase 2 still runs and reports the unhealthy state
        assert len(unhealthy) == 1

    def test_already_converged_skips_sleep(self, monkeypatch):
        """A stack that's already converged on first check exits without
        any sleep. Regression test for the loop ordering — check must come
        before sleep so fast deploys don't pay an unnecessary `interval` tax."""
        sleeps: list[float] = []
        monkeypatch.setattr("swarm.convergence.stack_ps", lambda *a, **kw: [["Running 5d"]])
        monkeypatch.setattr("swarm.convergence._all_replicas_healthy", lambda s: True)
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [("svc_web", "1/1")],
        )
        monkeypatch.setattr("swarm.convergence.time.sleep", lambda s: sleeps.append(s))

        converged, unhealthy = verify("mystack", timeout=5)
        assert converged is True
        assert unhealthy == []
        assert sleeps == []  # zero-delay exit

    def test_converges_after_pending(self, monkeypatch):
        """Phase 1 polls past transient Pending states until tasks settle."""
        call_count = 0

        def fake_stack_ps(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return [["Pending"]]
            return [["Running 10s"]]

        monkeypatch.setattr("swarm.convergence.stack_ps", fake_stack_ps)
        monkeypatch.setattr("swarm.convergence._all_replicas_healthy", lambda s: True)
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [("svc_web", "1/1")],
        )
        monkeypatch.setattr("swarm.convergence.time.sleep", lambda s: None)
        converged, unhealthy = verify("mystack", timeout=60, interval=1)
        assert converged is True
        assert unhealthy == []

    def test_backoff_grows_and_caps(self, monkeypatch):
        """Sleep interval grows 1.5x per iteration up to max_interval."""
        sleeps: list[float] = []
        monkeypatch.setattr("swarm.convergence.stack_ps", lambda *a, **kw: [["Pending"]])
        monkeypatch.setattr("swarm.convergence.time.sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [],
        )
        # Stop after 10 iterations regardless of "deadline" by tracking the
        # number of monotonic() calls and forcing the loop to exit.
        call_count = {"n": 0}

        def fake_monotonic():
            call_count["n"] += 1
            # First call sets the deadline anchor; return small. After ~10
            # loop iterations of monotonic check, jump past the deadline.
            return 0.0 if call_count["n"] <= 11 else 100.0

        monkeypatch.setattr("swarm.convergence.time.monotonic", fake_monotonic)

        verify("mystack", timeout=1, interval=2, max_interval=15)
        # Expected progression: 2, 3, 4.5, 6.75, 10.125, 15, 15, 15, ...
        assert sleeps[:6] == [2, 3, 4.5, 6.75, 10.125, 15]
        # Subsequent sleeps capped at 15
        for s in sleeps[6:]:
            assert s == 15


class TestTaskErrorsByService:
    """`_task_errors_by_service` maps each task's error string to its
    parent service name via `task_name_to_service`, returning a dict that
    groups all errors per service in a single stack-ps call."""

    def test_groups_by_service_via_task_name_to_service(self, monkeypatch):
        """task_name_to_service correctly maps stack-prefixed task names
        like 'accounts_postgres-init.1.abc123' back to their service."""
        monkeypatch.setattr(
            "swarm.convergence.stack_ps",
            lambda *a, **kw: [
                ["accounts_postgres-init.1.abc123", "task: non-zero exit (1)"],
                ["accounts_postgres-init.2.def456", "another error"],
                ["accounts_lldap.1.zzz", "lldap error"],
                ["other_stack_svc.1.qwe", "unrelated error"],
            ],
        )
        result = _task_errors_by_service("accounts")
        assert result["accounts_postgres-init"] == [
            "task: non-zero exit (1)",
            "another error",
        ]
        assert result["accounts_lldap"] == ["lldap error"]
        assert "other_stack_svc" in result  # all services bucketed

    def test_no_errors_returns_empty_map(self, monkeypatch):
        monkeypatch.setattr("swarm.convergence.stack_ps", lambda *a, **kw: [])
        assert _task_errors_by_service("mystack") == {}

    def test_skips_rows_without_error_text(self, monkeypatch):
        """Tasks with empty Error column are filtered out."""
        monkeypatch.setattr(
            "swarm.convergence.stack_ps",
            lambda *a, **kw: [
                ["mystack_a.1.id", ""],         # empty error
                ["mystack_b.1.id", "real err"],
                ["mystack_c.1.id"],             # malformed
            ],
        )
        result = _task_errors_by_service("mystack")
        assert result == {"mystack_b": ["real err"]}


class TestCollectUnhealthyOneQuery:
    """Regression: _collect_unhealthy fetches stack-ps ONCE for the whole
    stack, not once per unhealthy service."""

    def test_single_stack_ps_call_for_multiple_unhealthy(self, monkeypatch):
        ps_calls = []

        def fake_stack_ps(*a, **kw):
            ps_calls.append(kw.get("filters", []))
            return [
                ["mystack_a.1.id", "err A"],
                ["mystack_b.1.id", "err B"],
                ["mystack_c.1.id", "err C"],
            ]

        monkeypatch.setattr("swarm.convergence.stack_ps", fake_stack_ps)
        monkeypatch.setattr(
            "swarm.convergence.stack_services",
            lambda s: [
                ("mystack_a", "0/1"),
                ("mystack_b", "0/1"),
                ("mystack_c", "0/1"),
            ],
        )
        monkeypatch.setattr("swarm.convergence.is_task_complete", lambda s: False)

        result = _collect_unhealthy("mystack")
        # All 3 services unhealthy, but stack_ps called only once
        assert len(result) == 3
        assert len(ps_calls) == 1
        # Each service got its own error
        by_name = {r["name"]: r for r in result}
        assert by_name["mystack_a"]["errors"] == ["err A"]
        assert by_name["mystack_b"]["errors"] == ["err B"]
        assert by_name["mystack_c"]["errors"] == ["err C"]
