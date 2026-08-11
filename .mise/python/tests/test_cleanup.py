"""Tests for swarm.cleanup — versioned secret/config/network cleanup and node-wide prune."""

from conftest import make_completed

from swarm.cleanup import (
    SYSTEM_NETWORKS,
    VERSIONED_PATTERN,
    cleanup_orphaned_networks,
    cleanup_versioned_items,
    node_prune,
)


class TestVersionedPattern:
    """Pattern requires <base>_<7-hex>_<10-digit-epoch>$ exactly.

    Tighter than before to avoid false-positives on user-named items like
    'aws_iam_2024_01' (which used to match `_<hex>_<digits>$`)."""

    def test_matches_real_versioned(self):
        # Real-shape examples: 7 hex + 10 digit epoch
        assert VERSIONED_PATTERN.search("authelia_jwt_secret_4711029_1777558235")
        assert VERSIONED_PATTERN.search("grafana_config_eb4ff1a_1776824665")
        assert VERSIONED_PATTERN.search("base_abc1234_0000000000")

    def test_no_match_plain(self):
        assert not VERSIONED_PATTERN.search("my_secret")
        assert not VERSIONED_PATTERN.search("plain_config_name")

    def test_no_match_user_named_lookalike(self):
        """Tighter regex rejects user-named items that incidentally match
        the loose <hex>_<digits> shape."""
        assert not VERSIONED_PATTERN.search("aws_iam_2024_01")
        assert not VERSIONED_PATTERN.search("config_abc_42")
        assert not VERSIONED_PATTERN.search("foo_deadbee_123")  # 7 hex but only 3 digits

    def test_no_match_wrong_segment_lengths(self):
        # 6 hex chars (too short — git short SHAs are 7)
        assert not VERSIONED_PATTERN.search("foo_abcdef_1700000000")
        # 8 hex chars (too long)
        assert not VERSIONED_PATTERN.search("foo_abcdef01_1700000000")
        # 9 digit epoch (too short)
        assert not VERSIONED_PATTERN.search("foo_abc1234_170000000")
        # 11 digit epoch (too long)
        assert not VERSIONED_PATTERN.search("foo_abc1234_17000000000")


class TestCleanupVersionedItems:
    def test_removes_unused(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.cleanup.secret_list",
            lambda: [
                "auth_4711029_1777558235",
                "auth_4711029_1777558236",
                "static_secret",
            ],
        )
        removed_items = []
        monkeypatch.setattr("swarm.cleanup.secret_rm", lambda name: (removed_items.append(name), True)[1])

        assert cleanup_versioned_items("secret") == 2
        assert "static_secret" not in removed_items

    def test_skips_in_use(self, monkeypatch):
        monkeypatch.setattr("swarm.cleanup.secret_list", lambda: ["auth_4711029_1777558235"])
        monkeypatch.setattr("swarm.cleanup.secret_rm", lambda name: False)
        assert cleanup_versioned_items("secret") == 0

    def test_configs(self, monkeypatch):
        monkeypatch.setattr("swarm.cleanup.config_list", lambda: ["grafana_cfg_4711029_1777558235"])
        monkeypatch.setattr("swarm.cleanup.config_rm", lambda name: True)
        assert cleanup_versioned_items("config") == 1


class TestCleanupOrphanedNetworks:
    def test_removes_unattached(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.cleanup.network_list",
            lambda filters=None: ["net_alpha", "net_beta"],
        )
        removed = []
        monkeypatch.setattr("swarm.cleanup.network_rm", lambda n: (removed.append(n), True)[1])

        assert cleanup_orphaned_networks() == 2
        assert removed == ["net_alpha", "net_beta"]

    def test_skips_in_use(self, monkeypatch):
        """Docker refuses to remove attached networks → network_rm returns
        False → cleanup silently skips."""
        monkeypatch.setattr(
            "swarm.cleanup.network_list",
            lambda filters=None: ["net_alpha", "net_beta"],
        )
        # net_alpha is "in use", net_beta is removable
        monkeypatch.setattr("swarm.cleanup.network_rm", lambda n: n != "net_alpha")

        assert cleanup_orphaned_networks() == 1

    def test_skips_system_networks(self, monkeypatch):
        """Built-in 'ingress' must never be touched."""
        monkeypatch.setattr(
            "swarm.cleanup.network_list",
            lambda filters=None: ["ingress", "net_alpha"],
        )
        attempts = []
        monkeypatch.setattr("swarm.cleanup.network_rm", lambda n: attempts.append(n) or True)

        cleanup_orphaned_networks()
        assert "ingress" not in attempts
        assert "net_alpha" in attempts

    def test_passes_swarm_scope_filter(self, monkeypatch):
        """The list call must request scope=swarm so node-scoped networks
        (bridge, host, none, docker_gwbridge) are never touched."""
        captured: dict = {}

        def fake_list(filters=None):
            captured["filters"] = filters
            return []

        monkeypatch.setattr("swarm.cleanup.network_list", fake_list)
        monkeypatch.setattr("swarm.cleanup.network_rm", lambda n: True)
        cleanup_orphaned_networks()
        assert captured["filters"] == ["scope=swarm"]

    def test_system_networks_constant_intact(self):
        """Regression check: ingress is the canonical built-in we exclude."""
        assert "ingress" in SYSTEM_NETWORKS


class TestNodePrune:
    """node_prune just runs `docker system prune` per node and reports per-node
    success bool. No output parsing."""

    def test_returns_ok_per_node(self, monkeypatch):
        ssh_calls = []

        def fake_ssh(host, cmd, **kw):
            ssh_calls.append((host, cmd))
            return make_completed()  # exit 0

        monkeypatch.setattr("swarm.cleanup.ssh_node", fake_ssh)
        result = node_prune(["node1"])
        assert result == {"node1": True}
        assert ssh_calls == [("node1", "docker system prune --all --volumes --force")]

    def test_non_zero_exit_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.cleanup.ssh_node",
            lambda h, c, **kw: make_completed(returncode=1, stderr="boom"),
        )
        result = node_prune(["node1"])
        assert result == {"node1": False}

    def test_ssh_failure_returns_false(self, monkeypatch):
        def fail(h, c, **kw):
            raise Exception("unreachable")
        monkeypatch.setattr("swarm.cleanup.ssh_node", fail)
        assert node_prune(["node1"]) == {"node1": False}

    def test_multiple_nodes(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.cleanup.ssh_node",
            lambda h, c, **kw: make_completed(),
        )
        result = node_prune(["node1", "node2"])
        assert result == {"node1": True, "node2": True}

    def test_parallel_preserves_node_mapping(self, monkeypatch):
        """Concurrent prune across nodes maps result to correct node,
        regardless of which future completes first."""
        import time

        def slow_ssh(host, cmd, **kw):
            delays = {"alpha": 0.02, "bravo": 0.005, "charlie": 0.01}
            time.sleep(delays.get(host, 0))
            # bravo fails, others succeed
            return make_completed(returncode=1 if host == "bravo" else 0)

        monkeypatch.setattr("swarm.cleanup.ssh_node", slow_ssh)
        result = node_prune(["alpha", "bravo", "charlie"])
        assert result == {"alpha": True, "bravo": False, "charlie": True}

    def test_empty_nodes_returns_empty(self):
        assert node_prune([]) == {}
