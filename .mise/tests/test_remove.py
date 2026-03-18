"""Tests for swarm.remove — stack removal with drain wait."""


from swarm.remove import remove


class TestRemove:
    def test_not_deployed(self, monkeypatch):
        monkeypatch.setattr("swarm.remove.stack_list", lambda: ["other"])
        result = remove("stacks/infra/00_socket")
        assert result == 0

    def test_removes_and_drains(self, monkeypatch):
        monkeypatch.setattr("swarm.remove.stack_list", lambda: ["socket"])

        rm_called = []
        monkeypatch.setattr(
            "swarm.remove.docker_run",
            lambda *a, **kw: (rm_called.append(a), type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})())[1],
        )
        monkeypatch.setattr("swarm.remove.time.sleep", lambda s: None)

        result = remove("stacks/infra/00_socket", timeout=0, interval=1)
        assert result == 0
        assert ("stack", "rm", "socket") in rm_called

    def test_drain_timeout_warns(self, monkeypatch):
        monkeypatch.setattr("swarm.remove.stack_list", lambda: ["socket"])
        monkeypatch.setattr(
            "swarm.remove.docker_run",
            lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "task", "stderr": ""})(),
        )
        monkeypatch.setattr("swarm.remove.time.sleep", lambda s: None)

        # With timeout=0 and stack always reporting tasks, it should warn
        result = remove("stacks/infra/00_socket", timeout=0, interval=1)
        assert result == 0  # still exits 0, just warns

    def test_strips_nn_prefix(self, monkeypatch):
        monkeypatch.setattr("swarm.remove.stack_list", lambda: ["gateway-internal"])

        rm_name = []
        monkeypatch.setattr(
            "swarm.remove.docker_run",
            lambda *a, **kw: (
                rm_name.append(a[2]) if a[:2] == ("stack", "rm") else None,
                type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})(),
            )[1],
        )
        monkeypatch.setattr("swarm.remove.time.sleep", lambda s: None)

        remove("stacks/infra/30_gateway-internal", timeout=0)
        assert rm_name == ["gateway-internal"]
