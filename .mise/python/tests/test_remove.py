"""Tests for swarm.remove — stack removal with drain wait."""

from pathlib import Path

from swarm import SwarmError
from swarm.remove import remove_stack


class TestRemoveStack:
    def _mock_resolve(self, monkeypatch, path: str):
        monkeypatch.setattr("swarm.remove.resolve_stack_path", lambda ref: Path(path))

    def test_not_deployed(self, monkeypatch):
        self._mock_resolve(monkeypatch, "fake/ns/fake-stack")
        monkeypatch.setattr("swarm.remove.stack_list", lambda: ["other"])
        assert remove_stack("fake-stack") == 0

    def test_removes_and_drains(self, mock_docker, monkeypatch):
        self._mock_resolve(monkeypatch, "fake/ns/fake-stack")
        monkeypatch.setattr("swarm.remove.stack_list", lambda: ["fake-stack"])
        monkeypatch.setattr("swarm.remove.time.sleep", lambda s: None)

        # stack rm succeeds (default), stack ps returns rc=1 (drained)
        mock_docker.set_response(("stack", "ps"), returncode=1)
        result = remove_stack("fake-stack", timeout=0, interval=1)
        assert result == 0
        assert any(args[:2] == ("stack", "rm") for args in mock_docker.calls)

    def test_drain_timeout_warns(self, mock_docker, monkeypatch):
        self._mock_resolve(monkeypatch, "fake/ns/fake-stack")
        monkeypatch.setattr("swarm.remove.stack_list", lambda: ["fake-stack"])
        monkeypatch.setattr("swarm.remove.time.sleep", lambda s: None)
        # stack rm succeeds; stack ps returns rc=0 (still draining)
        mock_docker.set_response(("stack", "ps"), stdout="task line", returncode=0)
        result = remove_stack("fake-stack", timeout=0, interval=1)
        assert result == 0  # still exits 0, just warns

    def test_strips_nn_prefix(self, mock_docker, monkeypatch):
        self._mock_resolve(monkeypatch, "fake/ns/30_alpha-beta")
        monkeypatch.setattr("swarm.remove.stack_list", lambda: ["alpha-beta"])
        monkeypatch.setattr("swarm.remove.time.sleep", lambda s: None)
        mock_docker.set_response(("stack", "ps"), returncode=1)
        remove_stack("alpha-beta", timeout=0)
        rm_args = [a for a in mock_docker.calls if a[:2] == ("stack", "rm")]
        assert rm_args and rm_args[0][2] == "alpha-beta"

    def test_resolve_failure_returns_one(self, monkeypatch, caplog):
        def bad_resolve(ref):
            raise SwarmError("Stack not found: typo")
        monkeypatch.setattr("swarm.remove.resolve_stack_path", bad_resolve)
        rc = remove_stack("typo")
        assert rc == 1
        assert "Stack not found" in caplog.text

    def test_docker_rm_failure_returns_one(self, mock_docker, monkeypatch, caplog):
        self._mock_resolve(monkeypatch, "fake/ns/fake-stack")
        monkeypatch.setattr("swarm.remove.stack_list", lambda: ["fake-stack"])
        mock_docker.set_response(("stack", "rm"), returncode=1, stderr="boom")
        rc = remove_stack("fake-stack")
        assert rc == 1
        assert "docker stack rm failed" in caplog.text
