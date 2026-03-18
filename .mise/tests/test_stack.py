"""Tests for swarm._stack — stack name resolution and discovery."""

from pathlib import Path

from swarm._stack import find_stacks, stack_name


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
