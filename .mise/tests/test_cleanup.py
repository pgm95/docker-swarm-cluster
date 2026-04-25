"""Tests for swarm.cleanup — versioned secret/config cleanup and node-wide system prune."""


from conftest import make_completed
from swarm.cleanup import VERSIONED_PATTERN, cleanup_versioned_items, parse_prune_output, system_prune


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

        assert cleanup_versioned_items("secret") == 2
        assert "static_secret" not in removed_items

    def test_skips_in_use(self, monkeypatch):
        monkeypatch.setattr("swarm.cleanup.secret_list", lambda: ["auth_abc123_1700000000"])
        monkeypatch.setattr("swarm.cleanup.secret_rm", lambda name: False)
        assert cleanup_versioned_items("secret") == 0

    def test_configs(self, monkeypatch):
        monkeypatch.setattr("swarm.cleanup.config_list", lambda: ["grafana_cfg_abc123_1700000000"])
        monkeypatch.setattr("swarm.cleanup.config_rm", lambda name: True)
        assert cleanup_versioned_items("config") == 1


class TestParsePruneOutput:
    def test_full_output(self):
        out = """Deleted Containers:
abc123
def456

Deleted Networks:
mynet

Deleted Volumes:
vol1
vol2

Deleted Images:
untagged: lldap/lldap:rootless
untagged: sha256:4f1ecfedd717
deleted: sha256:4f1ecfedd717
deleted: sha256:9e997d914804
deleted: sha256:ff8683213ea6
untagged: alpine:latest
deleted: sha256:5b10f432ef3d

Deleted build cache objects:
aslgstcp1unlrlnwnhoz42icp
aemzoigywkt9vxyfi8g2479ag
xblyt8rp8azasvzs1uajs33lm

Total reclaimed space: 1.5GB
"""
        result = parse_prune_output(out)
        assert result["containers"] == 2
        assert result["networks"] == 1
        assert result["volumes"] == 2
        assert result["image_layers"] == 4  # only "deleted: sha256:" lines
        assert result["build_cache"] == 3
        assert result["reclaimed"] == "1.5GB"

    def test_empty(self):
        result = parse_prune_output("Total reclaimed space: 0B")
        assert result["containers"] == 0
        assert result["networks"] == 0
        assert result["volumes"] == 0
        assert result["image_layers"] == 0
        assert result["build_cache"] == 0
        assert result["reclaimed"] == "0B"

    def test_blank_terminates_section(self):
        out = """Deleted Containers:
abc

random unrelated line
Total reclaimed space: 5MB
"""
        result = parse_prune_output(out)
        assert result["containers"] == 1
        assert result["reclaimed"] == "5MB"

    def test_only_build_cache(self):
        out = """Deleted build cache objects:
foo
bar

Total reclaimed space: 200MB
"""
        result = parse_prune_output(out)
        assert result["build_cache"] == 2
        assert result["reclaimed"] == "200MB"


class TestSystemPrune:
    def test_returns_per_node_stats(self, monkeypatch):
        ssh_calls = []

        def fake_ssh(host, cmd, **kw):
            ssh_calls.append((host, cmd))
            return make_completed(
                stdout="Deleted Images:\ndeleted: sha256:abc\nTotal reclaimed space: 100MB",
            )

        monkeypatch.setattr("swarm.cleanup.ssh_node", fake_ssh)
        result = system_prune(["node1"])
        assert result["node1"]["image_layers"] == 1
        assert result["node1"]["reclaimed"] == "100MB"
        assert ssh_calls == [("node1", "docker system prune --all --volumes --force")]

    def test_ssh_failure(self, monkeypatch):
        def fail(h, c, **kw):
            raise Exception("unreachable")
        monkeypatch.setattr("swarm.cleanup.ssh_node", fail)
        result = system_prune(["node1"])
        assert result["node1"] is None

    def test_multiple_nodes(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.cleanup.ssh_node",
            lambda h, c, **kw: make_completed(stdout="Total reclaimed space: 10MB"),
        )
        result = system_prune(["node1", "node2"])
        assert result["node1"]["reclaimed"] == "10MB"
        assert result["node2"]["reclaimed"] == "10MB"
