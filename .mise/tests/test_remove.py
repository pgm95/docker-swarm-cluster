"""Tests for swarm.remove — stack removal with drain wait."""

from pathlib import Path

from swarm.remove import remove


class TestRemove:
    def _mock_resolve(self, monkeypatch, name: str, path: str):
        """Mock resolve_stack_path to return a Path with the given name."""
        monkeypatch.setattr(
            "swarm.remove.resolve_stack_path",
            lambda ref: Path(path),
        )

    def test_not_deployed(self, monkeypatch):
        self._mock_resolve(monkeypatch, "socket", "stacks/infra/00_socket")
        monkeypatch.setattr("swarm.remove.stack_list", lambda: ["other"])
        result = remove("socket")
        assert result == 0

    def test_removes_and_drains(self, monkeypatch):
        self._mock_resolve(monkeypatch, "socket", "stacks/infra/00_socket")
        monkeypatch.setattr("swarm.remove.stack_list", lambda: ["socket"])

        rm_called = []
        monkeypatch.setattr(
            "swarm.remove.docker_run",
            lambda *a, **kw: (rm_called.append(a), type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})())[1],
        )
        monkeypatch.setattr("swarm.remove.time.sleep", lambda s: None)

        result = remove("socket", timeout=0, interval=1)
        assert result == 0
        assert ("stack", "rm", "socket") in rm_called

    def test_drain_timeout_warns(self, monkeypatch):
        self._mock_resolve(monkeypatch, "socket", "stacks/infra/00_socket")
        monkeypatch.setattr("swarm.remove.stack_list", lambda: ["socket"])
        monkeypatch.setattr(
            "swarm.remove.docker_run",
            lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "task", "stderr": ""})(),
        )
        monkeypatch.setattr("swarm.remove.time.sleep", lambda s: None)

        result = remove("socket", timeout=0, interval=1)
        assert result == 0  # still exits 0, just warns

    def test_strips_nn_prefix(self, monkeypatch):
        self._mock_resolve(monkeypatch, "gateway-internal", "stacks/infra/30_gateway-internal")
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

        remove("gateway-internal", timeout=0)
        assert rm_name == ["gateway-internal"]
