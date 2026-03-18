"""Shared test fixtures for the swarm package.

All Docker/SSH calls are mocked at the subprocess boundary.
No live cluster required.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Sample data: realistic 3-node cluster (VM, LXC, VPS)
# ---------------------------------------------------------------------------

SAMPLE_NODES = [
    {
        "Description": {
            "Hostname": "swarm-vm",
            "Engine": {"EngineVersion": "29.2.1"},
        },
        "Spec": {
            "Role": "manager",
            "Labels": {
                "location": "onprem",
                "ip": "private",
                "type": "vm",
            },
            "Availability": "active",
        },
        "Status": {"State": "ready"},
        "ManagerStatus": {"Reachability": "reachable", "Leader": True},
    },
    {
        "Description": {
            "Hostname": "swarm-lxc",
            "Engine": {"EngineVersion": "29.2.1"},
        },
        "Spec": {
            "Role": "manager",
            "Labels": {
                "location": "onprem",
                "ip": "private",
                "type": "lxc",
                "storage": "true",
                "gpu": "true",
            },
            "Availability": "active",
        },
        "Status": {"State": "ready"},
        "ManagerStatus": {"Reachability": "reachable"},
    },
    {
        "Description": {
            "Hostname": "nerd1",
            "Engine": {"EngineVersion": "29.2.1"},
        },
        "Spec": {
            "Role": "worker",
            "Labels": {
                "location": "cloud",
                "ip": "public",
                "type": "vps",
            },
            "Availability": "active",
        },
        "Status": {"State": "ready"},
    },
]

SAMPLE_NODE_IDS = ["node1id", "node2id", "node3id"]


@pytest.fixture
def sample_nodes():
    """Realistic 3-node cluster JSON."""
    return SAMPLE_NODES


@pytest.fixture
def sample_node_ids():
    return SAMPLE_NODE_IDS


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def make_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Create a subprocess.CompletedProcess for mocking."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


@pytest.fixture
def mock_docker(monkeypatch):
    """Patch swarm._docker.run to return configurable responses.

    Returns a MockDocker instance. Set responses with mock.set_response().
    """

    class MockDocker:
        def __init__(self):
            self.calls: list[tuple] = []
            self._responses: dict[str, subprocess.CompletedProcess] = {}
            self._default = make_completed()

        def set_response(self, subcommand: str, stdout: str = "", stderr: str = "", returncode: int = 0):
            """Set response for a docker subcommand (first arg after 'docker')."""
            self._responses[subcommand] = make_completed(stdout, stderr, returncode)

        def __call__(self, *args, check=True, capture=True, input=None):
            self.calls.append(args)
            key = args[0] if args else ""
            result = self._responses.get(key, self._default)
            if check and result.returncode != 0:
                from swarm import DockerError
                raise DockerError(["docker", *args], result.returncode, result.stderr)
            return result

    mock = MockDocker()
    monkeypatch.setattr("swarm._docker.run", mock)
    return mock


@pytest.fixture
def mock_ssh(monkeypatch):
    """Patch swarm._ssh.ssh_node to return configurable responses.

    Returns a MockSSH instance.
    """

    class MockSSH:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []
            self._responses: dict[str, subprocess.CompletedProcess] = {}
            self._default = make_completed()

        def set_response(self, hostname: str, stdout: str = "", stderr: str = "", returncode: int = 0):
            self._responses[hostname] = make_completed(stdout, stderr, returncode)

        def __call__(self, hostname, command, stdin_data=None, check=True):
            self.calls.append((hostname, command))
            result = self._responses.get(hostname, self._default)
            if check and result.returncode != 0:
                from swarm import SSHError
                raise SSHError(hostname, result.returncode, result.stderr)
            return result

    mock = MockSSH()
    monkeypatch.setattr("swarm._ssh.ssh_node", mock)
    return mock


@pytest.fixture
def mock_subprocess(monkeypatch):
    """Patch subprocess.run globally for tests that bypass _docker/_ssh."""
    mock = MagicMock()
    mock.return_value = make_completed()
    monkeypatch.setattr("subprocess.run", mock)
    return mock


# ---------------------------------------------------------------------------
# Temp stack directory factory
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_stack(tmp_path):
    """Create a minimal stack directory structure.

    Usage:
        stack_dir = tmp_stack(
            compose="services: ...",
            secrets_yml="...",
            secrets_env="KEY=val",
        )
    """

    def _make(
        name: str = "test-stack",
        compose: str = "services: {}\n",
        secrets_yml: str | None = None,
        secrets_env: str | None = None,
        configs_yml: str | None = None,
        config_files: dict[str, str] | None = None,
        build_dirs: dict[str, dict[str, str]] | None = None,
    ) -> Path:
        stack_dir = tmp_path / name
        stack_dir.mkdir()
        (stack_dir / "compose.yml").write_text(compose)

        if secrets_yml is not None:
            (stack_dir / "secrets.yml").write_text(secrets_yml)
        if secrets_env is not None:
            (stack_dir / "secrets.env").write_text(secrets_env)
        if configs_yml is not None:
            (stack_dir / "configs.yml").write_text(configs_yml)
        if config_files:
            config_dir = stack_dir / "config"
            config_dir.mkdir()
            for fname, content in config_files.items():
                (config_dir / fname).write_text(content)
        if build_dirs:
            build_root = stack_dir / "build"
            build_root.mkdir()
            for svc_name, files in build_dirs.items():
                svc_dir = build_root / svc_name
                svc_dir.mkdir()
                for fname, content in files.items():
                    (svc_dir / fname).write_text(content)
        return stack_dir

    return _make
