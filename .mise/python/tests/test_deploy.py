"""Tests for swarm.deploy — content hashing, image builds, full pipeline."""

import re
from pathlib import Path

import pytest
from swarm import SwarmError
from swarm.deploy import (
    _prepare_stack,
    build_and_push,
    compute_content_hash,
    deploy_stack,
    discover_build_dirs,
    generate_deploy_version,
)


class TestComputeContentHash:
    def test_deterministic(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM alpine")
        (tmp_path / "script.sh").write_text("echo hi")
        h1 = compute_content_hash(tmp_path)
        h2 = compute_content_hash(tmp_path)
        assert h1 == h2

    def test_12_chars_hex(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM alpine")
        h = compute_content_hash(tmp_path)
        assert len(h) == 12
        assert all(c in "0123456789abcdef" for c in h)

    def test_excludes_md(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM alpine")
        h1 = compute_content_hash(tmp_path)
        (tmp_path / "README.md").write_text("docs")
        h2 = compute_content_hash(tmp_path)
        assert h1 == h2

    def test_content_change_changes_hash(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM alpine:3.18")
        h1 = compute_content_hash(tmp_path)
        (tmp_path / "Dockerfile").write_text("FROM alpine:3.19")
        h2 = compute_content_hash(tmp_path)
        assert h1 != h2

    def test_filename_matters(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        h1 = compute_content_hash(tmp_path)
        (tmp_path / "a.txt").unlink()
        (tmp_path / "b.txt").write_text("hello")
        h2 = compute_content_hash(tmp_path)
        assert h1 != h2

    def test_mode_bits_matter(self, tmp_path):
        """chmod +x on an entrypoint script must invalidate the cache.
        Docker's COPY preserves the executable bit, so two contexts with
        identical content but different perms produce different images."""
        import os

        script = tmp_path / "entrypoint.sh"
        script.write_text("#!/bin/sh\necho hi")
        h1 = compute_content_hash(tmp_path)
        # Add executable bit to owner
        os.chmod(script, script.stat().st_mode | 0o100)
        h2 = compute_content_hash(tmp_path)
        assert h1 != h2


class TestGenerateDeployVersion:
    def test_format(self, monkeypatch):
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: type(
            "R", (), {"stdout": "abc1234\n", "returncode": 0},
        )())
        version = generate_deploy_version()
        assert re.match(r"abc1234_\d+", version)

    def test_no_git_fallback(self, monkeypatch):
        def fail(*a, **kw):
            raise FileNotFoundError
        monkeypatch.setattr("subprocess.run", fail)
        version = generate_deploy_version()
        assert version.startswith("local_")


class TestDiscoverBuildDirs:
    def test_finds_builds(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GLOBAL_SWARM_OCI_REGISTRY", "reg.example.com")
        build_dir = tmp_path / "build" / "alpha"
        build_dir.mkdir(parents=True)
        (build_dir / "Dockerfile").write_text("FROM alpine")
        result = discover_build_dirs(tmp_path, "fakestack")
        assert len(result) == 1
        assert result[0]["service"] == "alpha"
        assert result[0]["var_name"] == "OCI_TAG_ALPHA"
        assert "reg.example.com/fakestack/alpha:" in result[0]["image"]

    def test_hyphenated_service_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GLOBAL_SWARM_OCI_REGISTRY", "reg")
        d = tmp_path / "build" / "foo-bar-baz"
        d.mkdir(parents=True)
        (d / "Dockerfile").write_text("FROM base")
        result = discover_build_dirs(tmp_path, "fakestack")
        assert result[0]["service"] == "foo-bar-baz"
        assert result[0]["var_name"] == "OCI_TAG_FOO_BAR_BAZ"
        assert "reg/fakestack/foo-bar-baz:" in result[0]["image"]

    def test_no_build_dir(self, tmp_path):
        assert discover_build_dirs(tmp_path, "mystack") == []

    def test_multiple_services(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GLOBAL_SWARM_OCI_REGISTRY", "reg")
        for svc in ["alpha", "beta"]:
            d = tmp_path / "build" / svc
            d.mkdir(parents=True)
            (d / "Dockerfile").write_text(f"FROM {svc}")
        result = discover_build_dirs(tmp_path, "stack")
        assert [r["service"] for r in result] == ["alpha", "beta"]


class TestBuildAndPush:
    def test_skips_existing(self, monkeypatch):
        monkeypatch.setattr("swarm.deploy.manifest_exists", lambda img: True)
        built = build_and_push("reg/img:tag", Path("/tmp/build"))
        assert built is False

    def test_builds_new(self, monkeypatch):
        monkeypatch.setattr("swarm.deploy.manifest_exists", lambda img: False)
        builds = []
        pushes = []
        monkeypatch.setattr("swarm.deploy.build", lambda tag, ctx: builds.append(tag))
        monkeypatch.setattr("swarm.deploy.push", lambda img: pushes.append(img))
        built = build_and_push("reg/img:tag", Path("/tmp/build"))
        assert built is True
        assert builds == ["reg/img:tag"]
        assert pushes == ["reg/img:tag"]


class TestPrepareStack:
    """_prepare_stack renders the compose, then drives discovery off the result.
    Tests stub the rendering helpers to control the payload."""

    def _stub_compose(self, monkeypatch, *, secrets: dict | None = None, configs: dict | None = None):
        """Make compose_config return YAML and compose_json return the parsed dict.

        `_prepare_stack` calls both helpers — `compose_config` for the YAML
        text passed to `docker stack deploy`, `compose_json` for the parsed
        document used in discovery. The two are imported into deploy.py so
        we patch them on `swarm.deploy.<name>`.
        """
        rendered = {"services": {}, "secrets": secrets or {}, "configs": configs or {}}
        monkeypatch.setattr("swarm.deploy.compose_config", lambda p, *a: "services: {}\n")
        monkeypatch.setattr("swarm.deploy.compose_json", lambda p: rendered)

    def test_returns_context_no_versioning(self, tmp_stack, monkeypatch):
        stack = tmp_stack(compose="services: {}\n")
        monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(stack / "compose.yml"))
        self._stub_compose(monkeypatch)  # empty secrets/configs
        ctx = _prepare_stack(stack)
        assert ctx["stack_name"] == "test-stack"
        assert ctx["stack_path"] == stack
        # No versioned secrets discovered → deploy_version blanked out
        assert ctx["deploy_version"] == ""
        assert ctx["stack_secrets"] == []
        assert ctx["builds"] == []
        # Rendered compose carried forward in ctx for re-use
        assert "services" in ctx["compose_yaml"]
        assert ctx["compose_json"]["secrets"] == {}

    def test_versioned_stack_returns_deploy_version(self, tmp_stack, monkeypatch):
        stack = tmp_stack(compose="services: {}\n", secrets_env="placeholder")
        monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(stack / "compose.yml"))
        monkeypatch.setattr("swarm.deploy.sops_decrypt", lambda f: [("S", "val")])
        # Render contains a versioned secret keyed off the (yet unknown) deploy_version.
        # Patch generate_deploy_version to a known value, then render with that suffix.
        monkeypatch.setattr("swarm.deploy.generate_deploy_version", lambda: "abc1234_1700000000")
        self._stub_compose(monkeypatch, secrets={
            "s": {"name": "s_abc1234_1700000000", "external": True},
        })
        monkeypatch.setattr("swarm.deploy.create_versioned_secrets", lambda *a, **kw: {"created": 1, "skipped": 0})

        ctx = _prepare_stack(stack)
        assert ctx["deploy_version"] == "abc1234_1700000000"
        assert ctx["stack_secrets"] == [("S", "val")]

    def test_decrypts_secrets_only_once(self, tmp_stack, monkeypatch):
        """secrets.env is decrypted once and the pairs feed both env-export
        and create_versioned_secrets."""
        stack = tmp_stack(compose="services: {}\n", secrets_env="placeholder")
        monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(stack / "compose.yml"))
        sops_calls = []
        monkeypatch.setattr(
            "swarm.deploy.sops_decrypt",
            lambda f: sops_calls.append(f) or [("DB_PASS", "secret")],
        )
        monkeypatch.setattr("swarm.deploy.generate_deploy_version", lambda: "v1")
        self._stub_compose(monkeypatch, secrets={
            "db_pass": {"name": "db_pass_v1", "external": True},
        })
        captured = {}
        monkeypatch.setattr(
            "swarm.deploy.create_versioned_secrets",
            lambda compose_json, version, stack_secrets=None: captured.setdefault("pairs", stack_secrets) or {"created": 1, "skipped": 0},
        )
        _prepare_stack(stack)
        assert len(sops_calls) == 1
        assert captured["pairs"] == [("DB_PASS", "secret")]

    def test_no_compose_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOPS_AGE_KEY_FILE", "/dev/null")
        with pytest.raises(SwarmError, match="compose.yml not found"):
            _prepare_stack(tmp_path)

    def test_missing_sops_key_raises(self, tmp_stack, monkeypatch):
        stack = tmp_stack(compose="services: {}\n")
        monkeypatch.setenv("SOPS_AGE_KEY_FILE", "")
        with pytest.raises(SwarmError, match="SOPS_AGE_KEY_FILE"):
            _prepare_stack(stack)


class TestDeployStack:
    """Full deploy pipeline orchestration. Each phase mocked at its boundary."""

    def _setup_happy(self, monkeypatch, stack):
        monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(stack / "compose.yml"))
        monkeypatch.setattr(
            "swarm.deploy._prepare_stack",
            lambda p: {
                "stack_name": "test-stack",
                "stack_path": p,
                "deploy_version": "",
                "stack_secrets": [],
                "builds": [],
                "compose_yaml": "services: {}\n",
                "compose_json": {"services": {}},
            },
        )
        monkeypatch.setattr("swarm.deploy._docker_stack_deploy", lambda *a, **kw: 0)
        monkeypatch.setattr("swarm.deploy._convergence.verify", lambda *a, **kw: (True, []))
        monkeypatch.setattr("swarm.deploy.stack_services", lambda s: [])

    def test_happy_path_returns_zero(self, tmp_stack, monkeypatch):
        stack = tmp_stack(compose="services: {}\n")
        self._setup_happy(monkeypatch, stack)
        assert deploy_stack(stack) == 0

    def test_prepare_failure_returns_one(self, tmp_stack, monkeypatch, caplog):
        stack = tmp_stack(compose="services: {}\n")
        def bad_prepare(p):
            raise SwarmError("missing secret AUTHENTIK_SECRET_KEY")
        monkeypatch.setattr("swarm.deploy._prepare_stack", bad_prepare)
        rc = deploy_stack(stack)
        assert rc == 1
        assert "AUTHENTIK_SECRET_KEY" in caplog.text

    def test_stack_deploy_failure_returns_one(self, tmp_stack, monkeypatch):
        stack = tmp_stack(compose="services: {}\n")
        self._setup_happy(monkeypatch, stack)
        monkeypatch.setattr("swarm.deploy._docker_stack_deploy", lambda *a, **kw: 1)
        assert deploy_stack(stack) == 1

    def test_convergence_timeout_returns_one(self, tmp_stack, monkeypatch, caplog):
        stack = tmp_stack(compose="services: {}\n")
        self._setup_happy(monkeypatch, stack)
        monkeypatch.setattr("swarm.deploy._convergence.verify", lambda *a, **kw: (False, []))
        rc = deploy_stack(stack)
        assert rc == 1
        assert "Convergence timeout" in caplog.text

    def test_post_converge_unhealthy_returns_one(self, tmp_stack, monkeypatch, caplog):
        stack = tmp_stack(compose="services: {}\n")
        self._setup_happy(monkeypatch, stack)
        monkeypatch.setattr(
            "swarm.deploy._convergence.verify",
            lambda *a, **kw: (True, [{"name": "test-stack_web", "replicas": "0/1"}]),
        )
        rc = deploy_stack(stack)
        assert rc == 1
        assert "Unhealthy services" in caplog.text
        assert "test-stack_web" in caplog.text
