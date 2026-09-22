"""Tests for the compose package: project discovery, host lookup, deploy, remove, validate, list."""

import json
import sys

import pytest
from conftest import make_completed

from compose import ToolError
from compose import _project as proj
from compose.deploy import compose_args, deploy_project
from compose.projects import main as projects_main
from compose.remove import remove_project
from compose.validate import main as validate_main
from compose.validate import missing_build_contexts, validate_project

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def projects_tree(tmp_path, monkeypatch):
    """compose/ tree: swarm-vps/haproxy (with a build context), other/app,
    an ignored _shared host, and a dir without compose.yml."""
    root = tmp_path / "compose"
    for rel in ("swarm-vps/haproxy", "other/app", "_shared/tools", "swarm-vps/notaproject"):
        (root / rel).mkdir(parents=True)
    for rel in ("swarm-vps/haproxy", "other/app", "_shared/tools"):
        (root / rel / "compose.yml").write_text("services: {}\n")
    (root / "swarm-vps/haproxy/build/endpoint").mkdir(parents=True)
    monkeypatch.setenv("COMPOSE_PROJECTS_DIR", str(root))
    for k in list(__import__("os").environ):
        if k.startswith("COMPOSE_HOST_"):
            monkeypatch.delenv(k)
    monkeypatch.delenv("COMPOSE_WAIT_TIMEOUT", raising=False)
    return root


class Recorder:
    """Records engine.run / engine.stream calls; responses keyed by leading compose verb."""

    def __init__(self):
        self.calls: list[tuple[tuple, str | None]] = []
        self.responses: dict[str, object] = {}

    def _verb(self, args):
        # compose_args() puts "compose --project-directory D -f F <verb> ..."
        return args[5] if args and args[0] == "compose" and len(args) > 5 else args[0]

    def run(self, *args, host=None, check=True, capture=True, input=None):
        self.calls.append((args, host))
        resp = self.responses.get(self._verb(args), make_completed())
        if isinstance(resp, Exception):
            raise resp
        if check and resp.returncode != 0:
            from core import DockerError
            raise DockerError(["docker", *args], resp.returncode, resp.stderr)
        return resp

    def stream(self, *args, host=None, input=None, line_prefixed=False):
        self.calls.append((args, host))
        resp = self.responses.get(self._verb(args))
        if isinstance(resp, Exception):
            raise resp

    def verbs(self):
        return [self._verb(a) for a, _ in self.calls]


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr("core.engine.run", r.run)
    monkeypatch.setattr("core.engine.stream", r.stream)
    return r


def docker_error(rc=1, stderr="boom"):
    from core import DockerError
    return DockerError(["docker"], rc, stderr)


# ---------------------------------------------------------------------------
# _project
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_all_projects_sorted_and_filtered(self, projects_tree):
        names = [proj.project_name(d) for d in proj.all_projects()]
        assert names == ["other/app", "swarm-vps/haproxy"]

    def test_missing_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv("COMPOSE_PROJECTS_DIR", str(tmp_path / "nope"))
        assert proj.all_projects() == []

    def test_name_and_role(self, projects_tree):
        d = projects_tree / "swarm-vps/haproxy"
        assert proj.project_name(d) == "swarm-vps/haproxy"
        assert proj.host_role(d) == "swarm-vps"


class TestResolve:
    def test_by_name(self, projects_tree):
        assert proj.resolve_project("swarm-vps/haproxy") == projects_tree / "swarm-vps/haproxy"

    def test_by_path(self, projects_tree):
        p = projects_tree / "other/app"
        assert proj.resolve_project(str(p)) == p

    def test_unknown(self, projects_tree):
        with pytest.raises(ToolError, match="not found"):
            proj.resolve_project("nope/nope")

    def test_path_outside_root_refused(self, projects_tree, tmp_path):
        outside = tmp_path / "stacks/infra/00_socket"
        outside.mkdir(parents=True)
        (outside / "compose.yml").write_text("services: {}\n")
        with pytest.raises(ToolError, match="outside"):
            proj.resolve_project(str(outside))

    def test_dir_without_compose_file(self, projects_tree):
        with pytest.raises(ToolError, match="not found"):
            proj.resolve_project(str(projects_tree / "swarm-vps/notaproject"))


class TestHost:
    def test_env_var_name(self):
        assert proj.host_env_var("swarm-vps") == "COMPOSE_HOST_SWARM_VPS"
        assert proj.host_env_var("docker-lxc") == "COMPOSE_HOST_DOCKER_LXC"

    def test_project_host(self, projects_tree, monkeypatch):
        monkeypatch.setenv("COMPOSE_HOST_SWARM_VPS", "ssh://root@swarm-vps-dev")
        assert proj.project_host(projects_tree / "swarm-vps/haproxy") == "ssh://root@swarm-vps-dev"

    def test_unset_is_error_naming_the_variable(self, projects_tree):
        with pytest.raises(ToolError, match="COMPOSE_HOST_SWARM_VPS is not set"):
            proj.project_host(projects_tree / "swarm-vps/haproxy")


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------


class TestComposeArgs:
    def test_pins_directory_and_file(self, tmp_path):
        args = compose_args(tmp_path, "up", "-d")
        assert args == ["compose", "--project-directory", str(tmp_path), "-f", str(tmp_path / "compose.yml"), "up", "-d"]


class TestDeploy:
    HOST = "ssh://root@swarm-vps-dev"

    def test_happy_path_call_sequence(self, projects_tree, monkeypatch, rec, capsys):
        monkeypatch.setenv("COMPOSE_HOST_SWARM_VPS", self.HOST)
        monkeypatch.setenv("COMPOSE_WAIT_TIMEOUT", "77")
        rec.responses["ps"] = make_completed(stdout="relay-haproxy\trunning\thealthy\nrelay-endpoint\trunning\thealthy\n")

        assert deploy_project("swarm-vps/haproxy") == 0

        assert rec.verbs() == ["config", "version", "up", "ps"]
        (config_args, config_host), (ver_args, ver_host), (up_args, up_host), (ps_args, ps_host) = rec.calls
        assert config_host is None and "--quiet" in config_args      # client side
        assert ver_args[0] == "version" and ver_host == self.HOST
        assert up_host == self.HOST
        assert up_args[5:] == ("up", "-d", "--build", "--remove-orphans", "--wait", "--wait-timeout", "77")
        assert ps_host == self.HOST and "--all" in ps_args
        err = capsys.readouterr().err
        assert "relay-haproxy" in err and "healthy" in err
        assert capsys.readouterr().out == ""

    def test_default_wait_timeout(self, projects_tree, monkeypatch, rec):
        monkeypatch.setenv("COMPOSE_HOST_SWARM_VPS", self.HOST)
        deploy_project("swarm-vps/haproxy")
        up_args = rec.calls[2][0]
        assert up_args[-2:] == ("--wait-timeout", "120")

    def test_unknown_project(self, projects_tree, rec, caplog):
        assert deploy_project("nope/nope") == 1
        assert rec.calls == []
        assert "not found" in caplog.text

    def test_unset_host_aborts_before_any_docker_call(self, projects_tree, rec, caplog):
        assert deploy_project("swarm-vps/haproxy") == 1
        assert rec.calls == []
        assert "COMPOSE_HOST_SWARM_VPS" in caplog.text

    def test_config_failure_stops_before_host(self, projects_tree, monkeypatch, rec, caplog):
        monkeypatch.setenv("COMPOSE_HOST_SWARM_VPS", self.HOST)
        rec.responses["config"] = make_completed(returncode=1, stderr="yaml: bad")
        assert deploy_project("swarm-vps/haproxy") == 1
        assert rec.verbs() == ["config"]
        assert "yaml: bad" in caplog.text

    def test_unreachable_host_stops_before_up(self, projects_tree, monkeypatch, rec, caplog):
        monkeypatch.setenv("COMPOSE_HOST_SWARM_VPS", self.HOST)
        rec.responses["version"] = make_completed(returncode=1, stderr="Cannot connect")
        assert deploy_project("swarm-vps/haproxy") == 1
        assert rec.verbs() == ["config", "version"]
        assert "unreachable" in caplog.text

    def test_up_failure_returns_one(self, projects_tree, monkeypatch, rec, caplog):
        monkeypatch.setenv("COMPOSE_HOST_SWARM_VPS", self.HOST)
        rec.responses["up"] = docker_error(rc=17)
        assert deploy_project("swarm-vps/haproxy") == 1
        assert rec.verbs() == ["config", "version", "up"]
        assert "exit 17" in caplog.text


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


class TestRemove:
    HOST = "ssh://root@swarm-vps"

    def test_down(self, projects_tree, monkeypatch, rec):
        monkeypatch.setenv("COMPOSE_HOST_SWARM_VPS", self.HOST)
        assert remove_project("swarm-vps/haproxy") == 0
        args, host = rec.calls[0]
        assert args[5:] == ("down", "--remove-orphans") and host == self.HOST

    def test_down_with_volumes(self, projects_tree, monkeypatch, rec):
        monkeypatch.setenv("COMPOSE_HOST_SWARM_VPS", self.HOST)
        remove_project("swarm-vps/haproxy", volumes=True)
        assert rec.calls[0][0][5:] == ("down", "--remove-orphans", "--volumes")

    def test_failure_returns_one(self, projects_tree, monkeypatch, rec, caplog):
        monkeypatch.setenv("COMPOSE_HOST_SWARM_VPS", self.HOST)
        rec.responses["down"] = docker_error(rc=3)
        assert remove_project("swarm-vps/haproxy") == 1
        assert "exit 3" in caplog.text

    def test_unset_host(self, projects_tree, rec):
        assert remove_project("swarm-vps/haproxy") == 1
        assert rec.calls == []


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def rendered(services: dict) -> str:
    return json.dumps({"services": services})


class TestMissingBuildContexts:
    def test_present_relative_and_absolute(self, projects_tree):
        d = projects_tree / "swarm-vps/haproxy"
        doc = {"services": {
            "a": {"build": {"context": "./build/endpoint"}},
            "b": {"build": str(d / "build/endpoint")},
            "c": {"image": "nginx"},
        }}
        assert missing_build_contexts(d, doc) == []

    def test_missing(self, projects_tree):
        d = projects_tree / "swarm-vps/haproxy"
        doc = {"services": {"a": {"build": {"context": "./build/nothere"}}, "b": {"build": "./also-nothere"}}}
        assert missing_build_contexts(d, doc) == ["a: ./build/nothere", "b: ./also-nothere"]


class TestValidateProject:
    def test_ok(self, projects_tree, rec, caplog):
        import logging
        caplog.set_level(logging.INFO)
        d = projects_tree / "swarm-vps/haproxy"
        rec.responses["config"] = make_completed(stdout=rendered({"a": {"build": {"context": "./build/endpoint"}}}))
        assert validate_project(d) is True
        assert "✓ swarm-vps/haproxy" in caplog.text
        assert rec.calls[0][1] is None                        # no daemon involved
        assert "--format" in rec.calls[0][0]

    def test_bad_document(self, projects_tree, rec, caplog):
        rec.responses["config"] = make_completed(returncode=1, stderr="services.a.image must be a string")
        assert validate_project(projects_tree / "other/app") is False
        assert "✗ other/app" in caplog.text and "must be a string" in caplog.text

    def test_missing_context(self, projects_tree, rec, caplog):
        rec.responses["config"] = make_completed(stdout=rendered({"a": {"build": {"context": "./gone"}}}))
        assert validate_project(projects_tree / "swarm-vps/haproxy") is False
        assert "a: ./gone" in caplog.text

    def test_invalid_json(self, projects_tree, rec, caplog):
        rec.responses["config"] = make_completed(stdout="not json")
        assert validate_project(projects_tree / "other/app") is False
        assert "invalid JSON" in caplog.text


class TestValidateMain:
    def test_all_projects_when_no_args(self, projects_tree, rec, monkeypatch):
        rec.responses["config"] = make_completed(stdout=rendered({}))
        monkeypatch.setattr(sys, "argv", ["compose.validate"])
        assert validate_main() == 0
        assert rec.verbs() == ["config", "config"]

    def test_one_failure_fails_run(self, projects_tree, rec, monkeypatch, caplog):
        rec.responses["config"] = make_completed(returncode=1, stderr="bad")
        monkeypatch.setattr(sys, "argv", ["compose.validate", "other/app"])
        assert validate_main() == 1
        assert "1 of 1" in caplog.text

    def test_unknown_project_arg(self, projects_tree, rec, monkeypatch, caplog):
        monkeypatch.setattr(sys, "argv", ["compose.validate", "nope/nope"])
        assert validate_main() == 1
        assert rec.calls == []


# ---------------------------------------------------------------------------
# projects (completion)
# ---------------------------------------------------------------------------


class TestProjectsList:
    def test_names_to_stdout_only(self, projects_tree, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["compose.projects"])
        assert projects_main() == 0
        out, err = capsys.readouterr()
        assert out.splitlines() == ["other/app", "swarm-vps/haproxy"]
        assert err == ""

    def test_paths(self, projects_tree, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["compose.projects", "--paths"])
        projects_main()
        out = capsys.readouterr().out.splitlines()
        assert out == [str(projects_tree / "other/app"), str(projects_tree / "swarm-vps/haproxy")]
