"""Tests for swarm.deploy — deploy preparation, content hashing, image builds."""

import re
import shlex

import pytest

from swarm.deploy import (
    _emit_export,
    build_and_push,
    compute_content_hash,
    detect_versioning,
    discover_build_dirs,
    generate_deploy_version,
    prepare,
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


class TestDetectVersioning:
    def test_has_deploy_version(self, tmp_path):
        (tmp_path / "secrets.yml").write_text("name: secret_${DEPLOY_VERSION}")
        assert detect_versioning(tmp_path) is True

    def test_no_deploy_version(self, tmp_path):
        (tmp_path / "compose.yml").write_text("services: {}")
        assert detect_versioning(tmp_path) is False

    def test_no_yml_files(self, tmp_path):
        assert detect_versioning(tmp_path) is False

    def test_in_configs_yml(self, tmp_path):
        (tmp_path / "configs.yml").write_text("name: cfg_${DEPLOY_VERSION}")
        assert detect_versioning(tmp_path) is True


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
        build_dir = tmp_path / "build" / "crowdsec"
        build_dir.mkdir(parents=True)
        (build_dir / "Dockerfile").write_text("FROM crowdsec")
        result = discover_build_dirs(tmp_path, "gateway-external")
        assert len(result) == 1
        assert result[0]["service"] == "crowdsec"
        assert result[0]["var_name"] == "OCI_TAG_CROWDSEC"
        assert "reg.example.com/gateway-external/crowdsec:" in result[0]["image"]

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
        built = build_and_push("reg/img:tag", "/tmp/build")
        assert built is False

    def test_builds_new(self, monkeypatch):
        monkeypatch.setattr("swarm.deploy.manifest_exists", lambda img: False)
        builds = []
        pushes = []
        monkeypatch.setattr("swarm.deploy.build", lambda tag, ctx: builds.append(tag))
        monkeypatch.setattr("swarm.deploy.push", lambda img: pushes.append(img))
        built = build_and_push("reg/img:tag", "/tmp/build")
        assert built is True
        assert builds == ["reg/img:tag"]
        assert pushes == ["reg/img:tag"]


class TestEmitExport:
    def test_simple_value(self, capsys):
        _emit_export("KEY", "value")
        assert capsys.readouterr().out == "export KEY=value\n"

    def test_quotes_special_chars(self, capsys):
        _emit_export("KEY", "hello world $var")
        output = capsys.readouterr().out.strip()
        assert output.startswith("export KEY=")
        # Value must be properly quoted
        val = output.split("=", 1)[1]
        assert shlex.split(val) == ["hello world $var"]


class TestPrepare:
    def test_outputs_exports(self, tmp_stack, monkeypatch, capsys):
        stack = tmp_stack(
            compose="services: {}\n",
        )
        monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(stack / "compose.yml"))  # just needs to exist
        prepare(str(stack))
        stdout = capsys.readouterr().out
        assert "export STACK_NAME=" in stdout
        assert "export STACK_PATH=" in stdout

    def test_versioned_stack(self, tmp_stack, monkeypatch, capsys):
        stack = tmp_stack(
            compose="services: {}\n",
            secrets_yml="s:\n    name: s_${DEPLOY_VERSION}\n    external: true\n",
            secrets_env="placeholder",
        )
        monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(stack / "compose.yml"))
        monkeypatch.setattr("swarm.deploy.sops_decrypt", lambda f: [("S", "val")])
        monkeypatch.setattr("swarm.deploy.validate_required_secrets", lambda p: None)
        monkeypatch.setattr("swarm.deploy.create_versioned_secrets", lambda p, v: {"created": 0, "skipped": 0, "filtered": 0})
        monkeypatch.setattr("swarm.deploy.validate_config_files", lambda p: None)
        prepare(str(stack))
        stdout = capsys.readouterr().out
        assert "export DEPLOY_VERSION=" in stdout

    def test_no_compose_raises(self, tmp_path, monkeypatch):
        from swarm import SwarmError
        monkeypatch.setenv("SOPS_AGE_KEY_FILE", "/dev/null")
        with pytest.raises(SwarmError, match="compose.yml not found"):
            prepare(str(tmp_path))
