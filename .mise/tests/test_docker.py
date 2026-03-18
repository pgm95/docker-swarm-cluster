"""Tests for swarm._docker — Docker CLI wrappers."""

import json

from swarm._docker import (
    config_rm,
    inspect_nodes,
    manifest_exists,
    secret_create,
    secret_exists,
    secret_rm,
    stack_list,
    stack_ps,
    stack_services,
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
    def test_secret_exists_true(self, mock_docker):
        mock_docker.set_response("secret", stdout="{}")
        assert secret_exists("my_secret") is True

    def test_secret_exists_false(self, mock_docker):
        mock_docker.set_response("secret", returncode=1, stderr="not found")
        assert secret_exists("my_secret") is False

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


class TestManifestExists:
    def test_exists(self, mock_docker):
        mock_docker.set_response("manifest", stdout="{}")
        assert manifest_exists("reg/img:tag") is True

    def test_not_exists(self, mock_docker):
        mock_docker.set_response("manifest", returncode=1, stderr="not found")
        assert manifest_exists("reg/img:tag") is False
