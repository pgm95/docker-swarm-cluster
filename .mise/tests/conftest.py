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

    Response keys can be:
      - a string: matches when the first arg equals the key (e.g. "stack").
      - a tuple of strings: matches when the args start with the tuple
        (e.g. ("stack", "config") matches `run("stack", "config", "-c", "-", ...)`
        but not `run("stack", "ls")`).

    Tuple keys are checked first (longest first), then string keys, then default.
    Lets one test fixture differentiate between sibling docker subcommands like
    `stack ls`, `stack ps`, `stack services`, `stack config`, `stack deploy`.
    """

    class MockDocker:
        def __init__(self):
            self.calls: list[tuple] = []
            self.inputs: list[str | None] = []   # parallel to calls; piped stdin per call
            self._tuple_responses: dict[tuple, subprocess.CompletedProcess] = {}
            self._string_responses: dict[str, subprocess.CompletedProcess] = {}
            self._default = make_completed()

        def set_response(
            self,
            subcommand: str | tuple[str, ...],
            stdout: str = "",
            stderr: str = "",
            returncode: int = 0,
        ):
            """Set response for a docker subcommand (string match) or arg prefix (tuple match)."""
            response = make_completed(stdout, stderr, returncode)
            if isinstance(subcommand, tuple):
                self._tuple_responses[subcommand] = response
            else:
                self._string_responses[subcommand] = response

        def _resolve(self, args: tuple) -> subprocess.CompletedProcess:
            for prefix in sorted(self._tuple_responses, key=len, reverse=True):
                if args[: len(prefix)] == prefix:
                    return self._tuple_responses[prefix]
            if args and args[0] in self._string_responses:
                return self._string_responses[args[0]]
            return self._default

        def __call__(self, *args, check=True, capture=True, input=None):
            self.calls.append(args)
            self.inputs.append(input)
            result = self._resolve(args)
            if check and result.returncode != 0:
                from swarm import DockerError
                raise DockerError(["docker", *args], result.returncode, result.stderr)
            return result

    mock = MockDocker()
    monkeypatch.setattr("swarm._docker.run", mock)
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
