"""Tests for swarm.validate — compose validation and bind mount checks."""

import os
from pathlib import Path

from conftest import SAMPLE_NODES, make_completed

from swarm._compose import _fixup_config
from swarm.validate import (
    _find_all_compose,
    _set_oci_tags,
    check_paths_on_node,
    collect_bind_mounts,
    extract_bind_mounts,
    validate_compose,
)


class TestExtractBindMounts:
    def test_finds_binds(self):
        compose = {
            "services": {
                "web": {
                    "volumes": [
                        {"type": "bind", "source": "/mnt/data", "target": "/data"},
                        {"type": "bind", "source": "/mnt/media", "target": "/media"},
                    ],
                },
            },
        }
        result = extract_bind_mounts(compose)
        assert result == {"web": ["/mnt/data", "/mnt/media"]}

    def test_filters_named_volumes(self):
        compose = {
            "services": {
                "db": {
                    "volumes": [
                        {"type": "volume", "source": "pg-data", "target": "/var/lib/postgresql"},
                        {"type": "bind", "source": "/mnt/backup", "target": "/backup"},
                    ],
                },
            },
        }
        result = extract_bind_mounts(compose)
        assert result == {"db": ["/mnt/backup"]}

    def test_no_volumes(self):
        compose = {"services": {"web": {}}}
        assert extract_bind_mounts(compose) == {}

    def test_no_bind_mounts(self):
        compose = {
            "services": {
                "web": {
                    "volumes": [{"type": "volume", "source": "data", "target": "/data"}],
                },
            },
        }
        assert extract_bind_mounts(compose) == {}

    def test_multiple_services(self):
        compose = {
            "services": {
                "web": {"volumes": [{"type": "bind", "source": "/a", "target": "/a"}]},
                "worker": {"volumes": [{"type": "bind", "source": "/b", "target": "/b"}]},
                "db": {"volumes": [{"type": "volume", "source": "v", "target": "/v"}]},
            },
        }
        result = extract_bind_mounts(compose)
        assert set(result.keys()) == {"web", "worker"}

    def test_empty_services(self):
        assert extract_bind_mounts({"services": {}}) == {}
        assert extract_bind_mounts({}) == {}


class TestFixupConfig:
    def test_removes_root_name(self):
        config = "name: mystack\nservices:\n  web:\n    image: nginx"
        result = _fixup_config(config)
        assert "name:" not in result.splitlines()[0]
        assert "services:" in result

    def test_unquotes_ports(self):
        config = '    published: "443"'
        result = _fixup_config(config)
        assert "published: 443" in result

    def test_preserves_indented_name(self):
        config = "services:\n  web:\n    container_name: myapp"
        result = _fixup_config(config)
        assert "container_name: myapp" in result

    def test_multiple_ports(self):
        config = '    published: "80"\n    published: "443"'
        result = _fixup_config(config)
        assert '"' not in result

    def test_unquotes_tmpfs_size(self):
        config = '      tmpfs:\n        size: "10485760"'
        result = _fixup_config(config)
        assert "size: 10485760" in result


class TestValidateCompose:
    def test_valid(self, mock_docker, monkeypatch):
        monkeypatch.setattr(
            "swarm.validate.compose_config",
            lambda *a: "name: test\nservices:\n  web:\n    image: nginx\n",
        )
        valid, err = validate_compose(Path("fake/ns/fake-stack/compose.yml"))
        assert valid is True
        assert err == ""

    def test_invalid_compose_config(self, monkeypatch):
        def fail(*a):
            raise Exception("bad yaml")
        monkeypatch.setattr("swarm.validate.compose_config", fail)
        valid, err = validate_compose(Path("fake/ns/fake-stack/compose.yml"))
        assert valid is False
        assert "bad yaml" in err

    def test_invalid_stack_config(self, mock_docker, monkeypatch):
        monkeypatch.setattr(
            "swarm.validate.compose_config",
            lambda *a: "services:\n  web:\n    image: nginx\n",
        )
        mock_docker.set_response(("stack", "config"), returncode=1, stderr="invalid config")
        valid, err = validate_compose(Path("fake/ns/fake-stack/compose.yml"))
        assert valid is False
        assert "invalid config" in err

    def test_reuses_yaml_when_provided(self, mock_docker, monkeypatch):
        """When yaml_text is passed in, compose_config is not called."""
        called = []
        monkeypatch.setattr(
            "swarm.validate.compose_config",
            lambda *a: called.append(a) or "should-not-be-called",
        )
        valid, _ = validate_compose(
            Path("fake/ns/fake-stack/compose.yml"),
            yaml_text="services:\n  web:\n    image: nginx\n",
        )
        assert valid is True
        assert called == []


class TestSetOciTags:
    def test_hyphenated_service_name(self, tmp_path, monkeypatch):
        """Validate must use the same OCI_TAG formula as deploy: hyphens → underscores."""
        d = tmp_path / "build" / "foo-bar-baz"
        d.mkdir(parents=True)
        (d / "Dockerfile").write_text("FROM base")
        # Clear any pre-existing env from earlier tests
        monkeypatch.delenv("OCI_TAG_FOO_BAR_BAZ", raising=False)
        monkeypatch.delenv("OCI_TAG_FOO-BAR-BAZ", raising=False)
        _set_oci_tags(tmp_path)
        assert "OCI_TAG_FOO_BAR_BAZ" in os.environ
        # The buggy old formula would have created OCI_TAG_FOO-BAR-BAZ
        assert "OCI_TAG_FOO-BAR-BAZ" not in os.environ

    def test_no_build_dir_is_noop(self, tmp_path, monkeypatch):
        """Stacks without a build/ directory should leave OCI_TAG_* env untouched."""
        monkeypatch.setattr("os.environ", {})
        _set_oci_tags(tmp_path)
        assert not any(k.startswith("OCI_TAG_") for k in os.environ)


class TestCollectBindMounts:
    def test_collects_paths_by_node(self, monkeypatch):
        compose_doc = {
            "services": {
                "web": {
                    "volumes": [{"type": "bind", "source": "/mnt/data", "target": "/data"}],
                    "deploy": {"placement": {"constraints": ["node.labels.type == vm"]}},
                },
            },
        }
        monkeypatch.setattr("swarm.validate.compose_json", lambda *a: compose_doc)
        result = collect_bind_mounts(Path("fake/ns/fake-stack/compose.yml"), SAMPLE_NODES)
        assert "swarm-vm" in result
        assert "/mnt/data" in result["swarm-vm"]

    def test_no_bind_mounts(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.validate.compose_json",
            lambda *a: {"services": {"web": {"image": "nginx"}}},
        )
        result = collect_bind_mounts(Path("fake/ns/fake-stack/compose.yml"), SAMPLE_NODES)
        assert result == {}

    def test_no_constraints_matches_first_node(self, monkeypatch):
        compose_doc = {
            "services": {
                "web": {
                    "volumes": [{"type": "bind", "source": "/mnt/data", "target": "/data"}],
                },
            },
        }
        monkeypatch.setattr("swarm.validate.compose_json", lambda *a: compose_doc)
        result = collect_bind_mounts(Path("fake/ns/fake-stack/compose.yml"), SAMPLE_NODES)
        # No constraints = matches first node
        assert len(result) == 1
        assert "/mnt/data" in next(iter(result.values()))


class TestCheckPathsOnNode:
    def test_missing_path(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.validate.ssh_node",
            lambda h, c, **kw: make_completed(stdout="MISSING"),
        )
        results = check_paths_on_node("swarm-vm", {"/mnt/data"})
        assert len(results) == 1
        assert results[0]["status"] == "missing"
        assert results[0]["path"] == "/mnt/data"

    def test_ok_path(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.validate.ssh_node",
            lambda h, c, **kw: make_completed(stdout="drwxr-xr-x:root:root"),
        )
        results = check_paths_on_node("swarm-vm", {"/mnt/data"})
        assert len(results) == 1
        assert results[0]["status"] == "ok"
        assert results[0]["path"] == "/mnt/data"
        assert results[0]["permissions"] == "drwxr-xr-x:root:root"

    def test_unreachable(self, monkeypatch):
        def fail(*a, **kw):
            raise Exception("ssh failed")
        monkeypatch.setattr("swarm.validate.ssh_node", fail)
        results = check_paths_on_node("swarm-vm", {"/mnt/data"})
        assert results[0]["status"] == "unreachable"

    def test_results_aligned_with_path_order(self, monkeypatch):
        """Multi-path SSH command order matches output line order."""
        monkeypatch.setattr(
            "swarm.validate.ssh_node",
            lambda h, c, **kw: make_completed(stdout="drwxr-xr-x:root:root\nMISSING"),
        )
        # sorted(): /a comes first, /b second
        results = check_paths_on_node("swarm-vm", {"/b", "/a"})
        # Find by path
        by_path = {r["path"]: r for r in results}
        assert by_path["/a"]["status"] == "ok"
        assert by_path["/b"]["status"] == "missing"

    def test_path_with_single_quote(self, monkeypatch):
        """shlex.quote handles paths with single quotes safely."""
        captured = {}
        def capture(host, cmd, **kw):
            captured["cmd"] = cmd
            return make_completed(stdout="drwxr-xr-x:root:root")
        monkeypatch.setattr("swarm.validate.ssh_node", capture)
        check_paths_on_node("swarm-vm", {"/mnt/foo's-data"})
        # The quoted path must not break the shell command (shlex.quote
        # produces something safe)
        assert "/mnt/foo" in captured["cmd"]
        # Should be quoted using shell-safe quoting
        assert "'" in captured["cmd"]

    def test_truncated_output_marks_tail_unreachable(self, monkeypatch):
        """If the shell chain produces fewer output lines than input paths
        (e.g., chain aborted mid-execution), unmatched tail paths must NOT
        be silently dropped — they're reported as unreachable."""
        # Two paths in, only one output line
        monkeypatch.setattr(
            "swarm.validate.ssh_node",
            lambda h, c, **kw: make_completed(stdout="drwxr-xr-x:root:root"),
        )
        results = check_paths_on_node("swarm-vm", {"/a", "/b"})
        # sorted: /a then /b
        by_path = {r["path"]: r for r in results}
        assert by_path["/a"]["status"] == "ok"
        assert by_path["/b"]["status"] == "unreachable"


class TestFindAllCompose:
    def test_only_stacks_with_compose(self, stacks_tree):
        (stacks_tree / "apps/mealie/compose.yml").write_text("services: {}\n")
        (stacks_tree / "infra/40_metrics/compose.yml").write_text("services: {}\n")
        assert _find_all_compose() == [
            stacks_tree / "apps/mealie/compose.yml",
            stacks_tree / "infra/40_metrics/compose.yml",
        ]
