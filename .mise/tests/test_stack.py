"""Tests for swarm._stack — stack name resolution and discovery."""

from pathlib import Path

import pytest

from swarm import SwarmError
from swarm._stack import find_namespaces, find_stacks, resolve_stack_path, stack_name, stacks_root


class TestStacksRoot:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("SWARM_STACKS_DIR", raising=False)
        assert stacks_root() == Path("stacks")

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SWARM_STACKS_DIR", "/tmp/alt-stacks")
        assert stacks_root() == Path("/tmp/alt-stacks")


class TestFindNamespaces:
    def test_lists_immediate_subdirs(self, tmp_path, monkeypatch):
        (tmp_path / "infra").mkdir()
        (tmp_path / "apps").mkdir()
        (tmp_path / "platform").mkdir()
        monkeypatch.setenv("SWARM_STACKS_DIR", str(tmp_path))
        result = find_namespaces()
        # Sorted alphabetically
        assert [p.name for p in result] == ["apps", "infra", "platform"]

    def test_excludes_underscore_prefixed(self, tmp_path, monkeypatch):
        (tmp_path / "infra").mkdir()
        (tmp_path / "_shared").mkdir()
        (tmp_path / "_archive").mkdir()
        monkeypatch.setenv("SWARM_STACKS_DIR", str(tmp_path))
        result = find_namespaces()
        assert [p.name for p in result] == ["infra"]

    def test_skips_files_at_top_level(self, tmp_path, monkeypatch):
        (tmp_path / "infra").mkdir()
        (tmp_path / "README.md").write_text("docs")
        monkeypatch.setenv("SWARM_STACKS_DIR", str(tmp_path))
        result = find_namespaces()
        assert [p.name for p in result] == ["infra"]

    def test_missing_root_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_STACKS_DIR", str(tmp_path / "nonexistent"))
        assert find_namespaces() == []


class TestStackName:
    def test_strips_nn_prefix(self):
        assert stack_name("any/path/00_alpha") == "alpha"

    def test_strips_nn_prefix_higher(self):
        assert stack_name("any/path/60_beta") == "beta"

    def test_no_prefix(self):
        assert stack_name("any/path/gamma") == "gamma"

    def test_path_object(self):
        assert stack_name(Path("any/path/31_kappa-lambda")) == "kappa-lambda"

    def test_only_strips_first_nn(self):
        assert stack_name("any/path/42_mu") == "mu"


class TestResolveStackPath:
    """Each test sets SWARM_STACKS_DIR explicitly to isolate from the real
    project tree. Mise's config.toml exports SWARM_STACKS_DIR as an absolute
    path, which would otherwise pierce monkeypatch.chdir."""

    def test_full_path(self, tmp_path, monkeypatch):
        stack = tmp_path / "stacks" / "infra" / "40_metrics"
        stack.mkdir(parents=True)
        monkeypatch.setenv("SWARM_STACKS_DIR", str(tmp_path / "stacks"))
        result = resolve_stack_path(str(stack))
        assert result == stack

    def test_dir_name_with_prefix(self, tmp_path, monkeypatch):
        infra = tmp_path / "stacks" / "infra"
        (infra / "40_metrics").mkdir(parents=True)
        monkeypatch.setenv("SWARM_STACKS_DIR", str(tmp_path / "stacks"))
        result = resolve_stack_path("40_metrics")
        assert result.name == "40_metrics"

    def test_bare_stack_name(self, tmp_path, monkeypatch):
        infra = tmp_path / "stacks" / "infra"
        (infra / "40_metrics").mkdir(parents=True)
        monkeypatch.setenv("SWARM_STACKS_DIR", str(tmp_path / "stacks"))
        result = resolve_stack_path("metrics")
        assert result.name == "40_metrics"

    def test_app_stack(self, tmp_path, monkeypatch):
        apps = tmp_path / "stacks" / "apps"
        (apps / "fakeapp").mkdir(parents=True)
        monkeypatch.setenv("SWARM_STACKS_DIR", str(tmp_path / "stacks"))
        result = resolve_stack_path("fakeapp")
        assert result.name == "fakeapp"

    def test_namespace_lookup_is_alphabetical(self, tmp_path, monkeypatch):
        """When a stack name exists in multiple namespaces, the alphabetically
        first namespace wins. Deterministic and namespace-agnostic — no
        special-casing for infra/apps."""
        (tmp_path / "stacks" / "infra" / "metrics").mkdir(parents=True)
        (tmp_path / "stacks" / "apps" / "metrics").mkdir(parents=True)
        monkeypatch.setenv("SWARM_STACKS_DIR", str(tmp_path / "stacks"))
        result = resolve_stack_path("metrics")
        # apps < infra alphabetically
        assert "apps" in str(result)

    def test_not_found_raises(self, tmp_path, monkeypatch):
        (tmp_path / "stacks" / "infra").mkdir(parents=True)
        (tmp_path / "stacks" / "apps").mkdir(parents=True)
        monkeypatch.setenv("SWARM_STACKS_DIR", str(tmp_path / "stacks"))
        with pytest.raises(SwarmError, match="Stack not found"):
            resolve_stack_path("nonexistent")

    def test_not_found_no_namespace_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_STACKS_DIR", str(tmp_path / "nonexistent"))
        with pytest.raises(SwarmError, match="Stack not found"):
            resolve_stack_path("anything")


class TestFindStacks:
    def test_finds_and_sorts(self, tmp_path):
        (tmp_path / "20_backup").mkdir()
        (tmp_path / "00_socket").mkdir()
        (tmp_path / "10_postgres").mkdir()
        result = find_stacks(tmp_path)
        assert [d.name for d in result] == ["00_socket", "10_postgres", "20_backup"]

    def test_reverse(self, tmp_path):
        (tmp_path / "20_backup").mkdir()
        (tmp_path / "00_socket").mkdir()
        result = find_stacks(tmp_path, reverse=True)
        assert [d.name for d in result] == ["20_backup", "00_socket"]

    def test_skips_files(self, tmp_path):
        (tmp_path / "00_socket").mkdir()
        (tmp_path / "README.md").write_text("hi")
        result = find_stacks(tmp_path)
        assert len(result) == 1

    def test_empty_dir(self, tmp_path):
        assert find_stacks(tmp_path) == []

    def test_nonexistent_dir(self):
        assert find_stacks("/nonexistent/path") == []
