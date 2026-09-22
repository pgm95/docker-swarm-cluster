"""Tests for core.engine: the daemon-agnostic docker wrapper and its per-call host override."""

from io import StringIO

import pytest
from conftest import make_completed

from core import DockerError, engine


class _FakeProc:
    def __init__(self, lines=(), returncode=0):
        self.returncode = returncode
        self.stdin = StringIO()
        self.stdout = iter(lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wait(self):
        return None


class TestDockerEnv:
    def test_no_host_leaves_environment_alone(self, monkeypatch):
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        monkeypatch.setenv("SWARM_HOST", "ssh://root@manager")
        env = engine.docker_env()
        # The engine never maps SWARM_HOST itself; that is swarm._docker's job.
        assert "DOCKER_HOST" not in env

    def test_no_host_keeps_shell_docker_host(self, monkeypatch):
        monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
        assert engine.docker_env()["DOCKER_HOST"] == "unix:///var/run/docker.sock"

    def test_host_overrides(self, monkeypatch):
        monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
        assert engine.docker_env("ssh://root@vps")["DOCKER_HOST"] == "ssh://root@vps"

    def test_does_not_mutate_process_environment(self, monkeypatch):
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        engine.docker_env("ssh://root@vps")
        import os
        assert "DOCKER_HOST" not in os.environ


class TestRun:
    def test_passes_host_in_env(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            seen["env"] = kw["env"]
            return make_completed(stdout="ok")

        monkeypatch.setattr("subprocess.run", fake_run)
        result = engine.run("version", host="ssh://root@vps")
        assert seen["cmd"] == ["docker", "version"]
        assert seen["env"]["DOCKER_HOST"] == "ssh://root@vps"
        assert result.stdout == "ok"

    def test_no_host_no_docker_host(self, monkeypatch):
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        seen = {}
        monkeypatch.setattr("subprocess.run", lambda cmd, **kw: seen.setdefault("env", kw["env"]) and make_completed())
        engine.run("version")
        assert "DOCKER_HOST" not in seen["env"]

    def test_check_raises(self, monkeypatch):
        monkeypatch.setattr("subprocess.run", lambda cmd, **kw: make_completed(returncode=1, stderr="boom"))
        with pytest.raises(DockerError) as e:
            engine.run("version")
        assert e.value.stderr == "boom"

    def test_check_false_returns(self, monkeypatch):
        monkeypatch.setattr("subprocess.run", lambda cmd, **kw: make_completed(returncode=1))
        assert engine.run("version", check=False).returncode == 1


class TestStream:
    def test_raw_branch_passes_host(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            seen["env"] = kw["env"]
            return make_completed()

        monkeypatch.setattr("subprocess.run", fake_run)
        engine.stream("build", "-t", "x", ".", host="ssh://root@vps")
        assert seen["env"]["DOCKER_HOST"] == "ssh://root@vps"

    def test_line_prefixed_branch_passes_host(self, monkeypatch):
        seen = {}

        def fake_popen(cmd, **kw):
            seen["env"] = kw["env"]
            return _FakeProc(lines=["hello\n"])

        monkeypatch.setattr("subprocess.Popen", fake_popen)
        engine.stream("compose", "up", host="ssh://root@vps", line_prefixed=True)
        assert seen["env"]["DOCKER_HOST"] == "ssh://root@vps"

    def test_line_prefixed_uses_output_prefix(self, monkeypatch, capsys):
        from core import output
        output.set_prefix("proj")
        monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: _FakeProc(lines=["one\n", "two\n"]))
        engine.stream("compose", "up", line_prefixed=True)
        output.set_prefix("")
        assert capsys.readouterr().err == "[proj] one\n[proj] two\n"

    def test_raw_branch_failure_raises(self, monkeypatch):
        monkeypatch.setattr("subprocess.run", lambda cmd, **kw: make_completed(returncode=2))
        with pytest.raises(DockerError):
            engine.stream("push", "img")


class TestSwarmWrapperDefaultsHost:
    """swarm._docker binds the engine to SWARM_HOST; every Swarm module relies on it."""

    def test_run_uses_swarm_host(self, monkeypatch):
        from swarm import _docker
        seen = {}
        monkeypatch.setenv("SWARM_HOST", "ssh://root@manager")
        monkeypatch.setattr("core.engine.run", lambda *a, **kw: seen.setdefault("host", kw["host"]) and make_completed())
        _docker.run("node", "ls")
        assert seen["host"] == "ssh://root@manager"

    def test_run_without_swarm_host_passes_none(self, monkeypatch):
        from swarm import _docker
        seen = {}
        monkeypatch.delenv("SWARM_HOST", raising=False)

        def fake(*a, **kw):
            seen["host"] = kw["host"]
            return make_completed()

        monkeypatch.setattr("core.engine.run", fake)
        _docker.run("node", "ls")
        assert seen["host"] is None

    def test_stream_uses_swarm_host(self, monkeypatch):
        from swarm import _docker
        seen = {}
        monkeypatch.setenv("SWARM_HOST", "ssh://root@manager")
        monkeypatch.setattr("core.engine.stream", lambda *a, **kw: seen.update(kw))
        _docker.stream("stack", "deploy", line_prefixed=True)
        assert seen["host"] == "ssh://root@manager"
        assert seen["line_prefixed"] is True
