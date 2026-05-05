"""Tests for swarm._docker — Docker CLI wrappers."""

import json

from swarm._docker import (
    config_rm,
    inspect_nodes,
    manifest_exists,
    network_list,
    network_rm,
    secret_create,
    secret_rm,
    stack_list,
    stack_ps,
    stack_services,
    task_name_to_service,
)
from conftest import SAMPLE_NODE_IDS, SAMPLE_NODES


class TestStackServices:
    def test_parses_output(self, mock_docker):
        mock_docker.set_response("stack", stdout="svc_web\t1/1\nsvc_db\t1/1\n")
        result = stack_services("mystack")
        assert result == [("svc_web", "1/1"), ("svc_db", "1/1")]

    def test_empty_stack(self, mock_docker):
        mock_docker.set_response("stack", stdout="")
        assert stack_services("mystack") == []


class TestStackPs:
    def test_parses_rows(self, mock_docker):
        mock_docker.set_response("stack", stdout="task1\tRunning\ntask2\tComplete\n")
        rows = stack_ps("mystack")
        assert rows == [["task1", "Running"], ["task2", "Complete"]]

    def test_empty(self, mock_docker):
        mock_docker.set_response("stack", stdout="")
        assert stack_ps("mystack") == []


class TestStackList:
    def test_lists_stacks(self, mock_docker):
        mock_docker.set_response("stack", stdout="socket\npostgres\nmetrics\n")
        assert stack_list() == ["socket", "postgres", "metrics"]

    def test_empty(self, mock_docker):
        mock_docker.set_response("stack", stdout="")
        assert stack_list() == []


class TestInspectNodes:
    def test_parses_json(self, monkeypatch):
        """Test inspect_nodes by patching run directly with a sequenced mock."""
        from conftest import make_completed
        calls = []

        def fake_run(*args, check=True, capture=True, input=None):
            calls.append(args)
            if args[:3] == ("node", "ls", "-q"):
                return make_completed(stdout="\n".join(SAMPLE_NODE_IDS))
            if args[0] == "node" and args[1] == "inspect":
                return make_completed(stdout=json.dumps(SAMPLE_NODES))
            return make_completed()

        monkeypatch.setattr("swarm._docker.run", fake_run)
        nodes = inspect_nodes()
        assert len(nodes) == 3
        assert nodes[0]["Description"]["Hostname"] == "swarm-vm"


class TestSecretOps:
    def test_secret_create(self, mock_docker):
        secret_create("my_secret_abc_123", "s3cret")
        assert len(mock_docker.calls) == 1
        assert "secret" in mock_docker.calls[0]

    def test_secret_rm_success(self, mock_docker):
        mock_docker.set_response("secret", stdout="")
        assert secret_rm("old_secret") is True

    def test_secret_rm_in_use(self, mock_docker):
        mock_docker.set_response("secret", returncode=1, stderr="in use")
        assert secret_rm("active_secret") is False


class TestConfigOps:
    def test_config_rm_success(self, mock_docker):
        mock_docker.set_response("config", stdout="")
        assert config_rm("old_config") is True


class TestNetworkOps:
    def test_network_list_basic(self, mock_docker):
        mock_docker.set_response("network", stdout="net_alpha\nnet_beta\ningress\n")
        assert network_list() == ["net_alpha", "net_beta", "ingress"]

    def test_network_list_passes_filters(self, mock_docker):
        mock_docker.set_response("network", stdout="net_alpha\n")
        network_list(filters=["scope=swarm", "driver=overlay"])
        args = mock_docker.calls[0]
        # Filters appear as adjacent ("--filter", value) pairs
        assert "--filter" in args
        assert "scope=swarm" in args
        assert "driver=overlay" in args

    def test_network_list_empty(self, mock_docker):
        mock_docker.set_response("network", stdout="")
        assert network_list() == []

    def test_network_rm_success(self, mock_docker):
        mock_docker.set_response("network", stdout="")
        assert network_rm("net_alpha") is True

    def test_network_rm_in_use(self, mock_docker):
        mock_docker.set_response("network", returncode=1, stderr="network is in use")
        assert network_rm("attached_net") is False


class TestManifestExists:
    def test_exists(self, mock_docker):
        mock_docker.set_response("manifest", stdout="{}")
        assert manifest_exists("reg/img:tag") is True

    def test_not_exists(self, mock_docker):
        mock_docker.set_response("manifest", returncode=1, stderr="not found")
        assert manifest_exists("reg/img:tag") is False


class TestTaskNameToService:
    def test_replicated_task(self):
        assert task_name_to_service("accounts_postgres-init.1.abc123") == "accounts_postgres-init"

    def test_global_task(self):
        # Global services use node-id instead of slot, but the parsing is identical.
        assert task_name_to_service("logging_alloy.swarm-vm-id.xyz") == "logging_alloy"

    def test_no_dots(self):
        assert task_name_to_service("just_a_service_name") == "just_a_service_name"

    def test_empty_string(self):
        assert task_name_to_service("") == ""


class _FakeProc:
    """Stand-in for subprocess.Popen, used as a context manager."""

    def __init__(self, lines: list[str], returncode: int = 0, with_stdin: bool = False):
        from io import StringIO
        self.returncode = returncode
        self.stdin = StringIO() if with_stdin else None
        self.stdout = iter(lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wait(self):
        return None


class TestStreamLinePrefixed:
    """`stream(line_prefixed=True)` reads docker's output line by line and
    prepends `_output.get_stack_prefix()` to each. Goal: lines like
    `Creating service X` from `docker stack deploy` get attributed to the
    current stack visually."""

    def test_prefixes_each_line(self, monkeypatch, capsys):
        from swarm import _docker, _output

        _output.init_stack_prefix("mystack")
        proc = _FakeProc(
            lines=[
                "Creating service mystack_web\n",
                "Creating config mystack_cfg\n",
                "Updating service mystack_db (id: abc)\n",
            ],
            with_stdin=True,
        )
        monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: proc)
        _docker.stream("stack", "deploy", "-c", "-", "mystack",
                       input="services: {}\n", line_prefixed=True)

        captured = capsys.readouterr().err
        assert "[mystack] Creating service mystack_web" in captured
        assert "[mystack] Creating config mystack_cfg" in captured
        assert "[mystack] Updating service mystack_db (id: abc)" in captured
        _output.init_stack_prefix("")

    def test_no_prefix_when_unset(self, monkeypatch, capsys):
        from swarm import _docker, _output
        _output.init_stack_prefix("")
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda cmd, **kw: _FakeProc(lines=["plain line\n"]),
        )
        _docker.stream("stack", "deploy", line_prefixed=True)
        assert capsys.readouterr().err == "plain line\n"

    def test_non_zero_exit_raises(self, monkeypatch):
        import pytest

        from swarm import _docker, DockerError
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda cmd, **kw: _FakeProc(lines=[], returncode=1),
        )
        with pytest.raises(DockerError):
            _docker.stream("stack", "deploy", line_prefixed=True)

    def test_dockererror_carries_output_tail(self, monkeypatch):
        """On non-zero exit, DockerError gets the tail of streamed output —
        not an empty string. Operators must see Docker's actual error."""
        import pytest

        from swarm import _docker, DockerError
        proc = _FakeProc(
            lines=["pulling image...\n", "error: image not found\n"],
            returncode=1,
        )
        monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: proc)
        with pytest.raises(DockerError) as exc_info:
            _docker.stream("stack", "deploy", line_prefixed=True)
        assert "image not found" in exc_info.value.stderr


class TestComposeConfigStdinPipe:
    def test_pipes_combined_yaml_via_stdin(self, mock_docker, tmp_path, monkeypatch):
        """compose_config concatenates anchors+compose and pipes via input=."""
        from swarm._compose import _read_anchors_file, compose_config

        anchors = tmp_path / "anchors.yml"
        anchors.write_text("x-anchor: &a value\n")
        monkeypatch.setenv("SWARM_ANCHORS_FILE", str(anchors))
        # The path-keyed cache is fresh for new tmp_path values, but clear
        # to keep tests independent.
        _read_anchors_file.cache_clear()

        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yml"
        compose_file.write_text("services:\n  web:\n    image: nginx\n")

        mock_docker.set_response("compose", stdout="services:\n  web:\n    image: nginx\n")
        compose_config(compose_file)

        assert len(mock_docker.calls) == 1
        args = mock_docker.calls[0]
        assert args[0] == "compose"
        assert "-f" in args
        # The "-f" value is "-" meaning stdin
        assert args[args.index("-f") + 1] == "-"
        # The combined anchors+compose YAML was piped on stdin
        piped = mock_docker.inputs[0]
        assert "x-anchor: &a value" in piped
        assert "image: nginx" in piped

    def test_missing_anchors_file_renders_compose_alone(self, mock_docker, tmp_path, monkeypatch):
        """SWARM_ANCHORS_FILE pointing at a nonexistent file is not an error;
        the lib renders the stack's compose.yml as-is."""
        from swarm._compose import _read_anchors_file, compose_config

        monkeypatch.setenv("SWARM_ANCHORS_FILE", str(tmp_path / "does-not-exist.yml"))
        _read_anchors_file.cache_clear()

        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yml"
        compose_file.write_text("services:\n  web:\n    image: nginx\n")

        mock_docker.set_response("compose", stdout="services: {}\n")
        compose_config(compose_file)

        piped = mock_docker.inputs[0]
        assert "image: nginx" in piped
        assert "x-anchor" not in piped


class TestMockDockerTupleKeys:
    def test_tuple_match_takes_precedence_over_string(self, mock_docker):
        mock_docker.set_response("stack", stdout="default-stack-response")
        mock_docker.set_response(("stack", "config"), stdout="config-specific-response")
        # stack ls falls back to the string key
        assert stack_list() == ["default-stack-response"]

    def test_longest_tuple_wins(self, mock_docker):
        mock_docker.set_response(("stack",), stdout="short\n")
        mock_docker.set_response(("stack", "ls"), stdout="long\n")
        # stack_list calls run("stack", "ls", ...) — the 2-tuple should win
        assert stack_list() == ["long"]
