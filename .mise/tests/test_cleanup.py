"""Tests for swarm.cleanup — versioned secret/config cleanup and node pruning."""


from conftest import make_completed
from swarm.cleanup import VERSIONED_PATTERN, cleanup_versioned_items, prune_containers, prune_images, prune_volumes


class TestVersionedPattern:
    def test_matches_versioned(self):
        assert VERSIONED_PATTERN.search("authelia_jwt_secret_abc123_1700000000")
        assert VERSIONED_PATTERN.search("grafana_config_def456_1700000001")

    def test_no_match_plain(self):
        assert not VERSIONED_PATTERN.search("my_secret")
        assert not VERSIONED_PATTERN.search("plain_config_name")


class TestCleanupVersionedItems:
    def test_removes_unused(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.cleanup.secret_list",
            lambda: ["auth_abc123_1700000000", "auth_def456_1700000001", "static_secret"],
        )
        removed_items = []
        monkeypatch.setattr("swarm.cleanup.secret_rm", lambda name: (removed_items.append(name), True)[1])

        result = cleanup_versioned_items("secret")
        assert result["removed"] == 2
        assert "static_secret" not in removed_items

    def test_skips_in_use(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.cleanup.secret_list",
            lambda: ["auth_abc123_1700000000"],
        )
        monkeypatch.setattr("swarm.cleanup.secret_rm", lambda name: False)

        result = cleanup_versioned_items("secret")
        assert result["removed"] == 0

    def test_configs(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.cleanup.config_list",
            lambda: ["grafana_cfg_abc123_1700000000"],
        )
        monkeypatch.setattr("swarm.cleanup.config_rm", lambda name: True)

        result = cleanup_versioned_items("config")
        assert result["removed"] == 1


class TestPruneContainers:
    def test_parses_output(self, monkeypatch):
        from conftest import make_completed
        monkeypatch.setattr(
            "swarm.cleanup.ssh_node",
            lambda h, c, **kw: make_completed(stdout="abc123\ndef456\nTotal reclaimed space: 10MB"),
        )
        result = prune_containers(["node1"])
        assert result["node1"] == 2

    def test_empty_output(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.cleanup.ssh_node",
            lambda h, c, **kw: make_completed(stdout="Total reclaimed space: 0B"),
        )
        result = prune_containers(["node1"])
        assert result["node1"] == 0


class TestPruneImages:
    def test_parses_output(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.cleanup.ssh_node",
            lambda h, c, **kw: make_completed(
                stdout="deleted: sha256:abc\ndeleted: sha256:def\nTotal reclaimed space: 150MB",
            ),
        )
        result = prune_images(["node1"])
        assert result["node1"]["count"] == 2
        assert result["node1"]["reclaimed"] == "150MB"

    def test_no_images(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.cleanup.ssh_node",
            lambda h, c, **kw: make_completed(stdout="Total reclaimed space: 0B"),
        )
        result = prune_images(["node1"])
        assert result["node1"]["count"] == 0
        assert result["node1"]["reclaimed"] == "0B"

    def test_ssh_failure(self, monkeypatch):
        def fail(h, c, **kw):
            raise Exception("unreachable")
        monkeypatch.setattr("swarm.cleanup.ssh_node", fail)
        result = prune_images(["node1"])
        assert result["node1"]["count"] == 0


class TestPruneVolumes:
    def test_parses_output(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.cleanup.ssh_node",
            lambda h, c, **kw: make_completed(
                stdout="abc123\ndef456\nTotal reclaimed space: 50MB",
            ),
        )
        result = prune_volumes(["node1"])
        assert result["node1"]["count"] == 2
        assert result["node1"]["reclaimed"] == "50MB"

    def test_no_volumes(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.cleanup.ssh_node",
            lambda h, c, **kw: make_completed(stdout="Total reclaimed space: 0B"),
        )
        result = prune_volumes(["node1"])
        assert result["node1"]["count"] == 0

    def test_multiple_nodes(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.cleanup.ssh_node",
            lambda h, c, **kw: make_completed(stdout="abc123\nTotal reclaimed space: 10MB"),
        )
        result = prune_volumes(["node1", "node2"])
        assert len(result) == 2
        assert result["node1"]["count"] == 1
        assert result["node2"]["count"] == 1
