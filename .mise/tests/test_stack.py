"""Tests for swarm._stack — stack name resolution and discovery."""

from pathlib import Path

import pytest

from swarm import SwarmError
from swarm._stack import find_stacks, resolve_stack_path, stack_name


class TestStackName:
    def test_strips_nn_prefix(self):
        assert stack_name("stacks/infra/00_socket") == "socket"

    def test_strips_nn_prefix_higher(self):
        assert stack_name("stacks/infra/60_accounts") == "accounts"

    def test_no_prefix(self):
        assert stack_name("stacks/apps/mealie") == "mealie"

    def test_path_object(self):
        assert stack_name(Path("stacks/infra/31_gateway-external")) == "gateway-external"

    def test_only_strips_first_nn(self):
        assert stack_name("stacks/infra/42_logging") == "logging"


class TestResolveStackPath:
    def test_full_path(self, tmp_path, monkeypatch):
        stack = tmp_path / "stacks" / "infra" / "40_metrics"
        stack.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        result = resolve_stack_path(str(stack))
        assert result == stack

    def test_dir_name_with_prefix(self, tmp_path, monkeypatch):
        infra = tmp_path / "stacks" / "infra"
        (infra / "40_metrics").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        result = resolve_stack_path("40_metrics")
        assert result.name == "40_metrics"

    def test_bare_stack_name(self, tmp_path, monkeypatch):
        infra = tmp_path / "stacks" / "infra"
        (infra / "40_metrics").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        result = resolve_stack_path("metrics")
        assert result.name == "40_metrics"

    def test_app_stack(self, tmp_path, monkeypatch):
        apps = tmp_path / "stacks" / "apps"
        (apps / "mealie").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        result = resolve_stack_path("mealie")
        assert result.name == "mealie"

    def test_infra_preferred_over_apps(self, tmp_path, monkeypatch):
        (tmp_path / "stacks" / "infra" / "metrics").mkdir(parents=True)
        (tmp_path / "stacks" / "apps" / "metrics").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        result = resolve_stack_path("metrics")
        assert "infra" in str(result)

    def test_not_found_raises(self, tmp_path, monkeypatch):
        (tmp_path / "stacks" / "infra").mkdir(parents=True)
        (tmp_path / "stacks" / "apps").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SwarmError, match="Stack not found"):
            resolve_stack_path("nonexistent")

    def test_not_found_no_namespace_dirs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
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
