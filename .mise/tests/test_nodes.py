"""Tests for swarm.nodes — node discovery and constraint matching."""

import json

from conftest import SAMPLE_NODES
from swarm.nodes import (
    _find_matching_node,
    _match_constraint,
    _match_labels,
    _parse_constraints,
    get_service_node,
    get_swarm_nodes,
)


# ---------------------------------------------------------------------------
# Pure function tests (no mocking needed)
# ---------------------------------------------------------------------------


class TestMatchLabels:
    def test_all_match(self):
        labels = {"location": "onprem", "type": "vm"}
        assert _match_labels(labels, ["location=onprem", "type=vm"]) is True

    def test_partial_mismatch(self):
        labels = {"location": "onprem", "type": "lxc"}
        assert _match_labels(labels, ["location=onprem", "type=vm"]) is False

    def test_missing_label(self):
        labels = {"location": "onprem"}
        assert _match_labels(labels, ["type=vm"]) is False

    def test_empty_filters(self):
        assert _match_labels({"a": "b"}, []) is True


class TestParseConstraints:
    def test_standard(self):
        result = _parse_constraints(["node.labels.type == vm"])
        assert result == [("node.labels.type", "==", "vm")]

    def test_not_equals(self):
        result = _parse_constraints(["node.role != manager"])
        assert result == [("node.role", "!=", "manager")]

    def test_multiple(self):
        result = _parse_constraints([
            "node.labels.location == onprem",
            "node.labels.type == vm",
        ])
        assert len(result) == 2

    def test_malformed_skipped(self):
        result = _parse_constraints(["invalid"])
        assert result == []


class TestMatchConstraint:
    def test_label_equals(self):
        node = {"labels": {"type": "vm"}, "role": "manager", "hostname": "vm1"}
        assert _match_constraint("node.labels.type", "==", "vm", node) is True

    def test_label_not_equals(self):
        node = {"labels": {"type": "vm"}, "role": "manager", "hostname": "vm1"}
        assert _match_constraint("node.labels.type", "!=", "lxc", node) is True

    def test_label_missing(self):
        node = {"labels": {}, "role": "worker", "hostname": "vps1"}
        assert _match_constraint("node.labels.type", "==", "vm", node) is False

    def test_role(self):
        node = {"labels": {}, "role": "manager", "hostname": "vm1"}
        assert _match_constraint("node.role", "==", "manager", node) is True

    def test_hostname(self):
        node = {"labels": {}, "role": "worker", "hostname": "nerd1"}
        assert _match_constraint("node.hostname", "==", "nerd1", node) is True

    def test_unknown_field(self):
        node = {"labels": {}, "role": "worker", "hostname": "x"}
        assert _match_constraint("node.unknown", "==", "x", node) is False


class TestFindMatchingNode:
    NODES = [
        {"hostname": "vm1", "labels": {"location": "onprem", "type": "vm"}, "role": "manager"},
        {"hostname": "lxc1", "labels": {"location": "onprem", "type": "lxc"}, "role": "manager"},
        {"hostname": "vps1", "labels": {"location": "cloud", "type": "vps"}, "role": "worker"},
    ]

    def test_finds_vm(self):
        constraints = [("node.labels.type", "==", "vm")]
        assert _find_matching_node(constraints, self.NODES) == "vm1"

    def test_finds_cloud(self):
        constraints = [("node.labels.location", "==", "cloud")]
        assert _find_matching_node(constraints, self.NODES) == "vps1"

    def test_multiple_constraints(self):
        constraints = [
            ("node.labels.location", "==", "onprem"),
            ("node.labels.type", "==", "lxc"),
        ]
        assert _find_matching_node(constraints, self.NODES) == "lxc1"

    def test_no_match(self):
        constraints = [("node.labels.type", "==", "gpu")]
        assert _find_matching_node(constraints, self.NODES) is None

    def test_empty_constraints_matches_first(self):
        assert _find_matching_node([], self.NODES) == "vm1"


# ---------------------------------------------------------------------------
# Integration tests (mock docker calls)
# ---------------------------------------------------------------------------


class TestGetSwarmNodes:
    def test_no_filter(self, monkeypatch):
        monkeypatch.setattr("swarm.nodes.inspect_nodes", lambda: SAMPLE_NODES)
        nodes = get_swarm_nodes()
        assert len(nodes) == 3
        hostnames = [n["hostname"] for n in nodes]
        assert "swarm-vm" in hostnames
        assert "nerd1" in hostnames

    def test_with_filter(self, monkeypatch):
        monkeypatch.setattr("swarm.nodes.inspect_nodes", lambda: SAMPLE_NODES)
        nodes = get_swarm_nodes(["location=onprem"])
        assert len(nodes) == 2
        hostnames = [n["hostname"] for n in nodes]
        assert "nerd1" not in hostnames

    def test_multiple_filters_and(self, monkeypatch):
        monkeypatch.setattr("swarm.nodes.inspect_nodes", lambda: SAMPLE_NODES)
        nodes = get_swarm_nodes(["location=onprem", "type=vm"])
        assert len(nodes) == 1
        assert nodes[0]["hostname"] == "swarm-vm"

    def test_no_match(self, monkeypatch):
        monkeypatch.setattr("swarm.nodes.inspect_nodes", lambda: SAMPLE_NODES)
        nodes = get_swarm_nodes(["type=gpu"])
        assert nodes == []


class TestGetServiceNode:
    COMPOSE_JSON = json.dumps({
        "services": {
            "web": {
                "deploy": {
                    "placement": {
                        "constraints": ["node.labels.location == cloud"],
                    },
                },
            },
            "db": {
                "deploy": {
                    "placement": {
                        "constraints": [
                            "node.labels.location == onprem",
                            "node.labels.type == lxc",
                        ],
                    },
                },
            },
            "worker": {},
        },
    })

    def test_resolves_services(self, monkeypatch):
        monkeypatch.setattr("swarm.nodes.compose_config", lambda *a, **kw: self.COMPOSE_JSON)
        monkeypatch.setattr("swarm.nodes.inspect_nodes", lambda: SAMPLE_NODES)
        result = get_service_node("fake/compose.yml")
        mapping = dict(result)
        assert mapping["web"] == "nerd1"
        assert mapping["db"] == "swarm-lxc"
        assert mapping["worker"] == "swarm-vm"  # no constraints → first node

    def test_unresolved(self, monkeypatch):
        compose = json.dumps({
            "services": {
                "svc": {
                    "deploy": {
                        "placement": {
                            "constraints": ["node.labels.type == nonexistent"],
                        },
                    },
                },
            },
        })
        monkeypatch.setattr("swarm.nodes.compose_config", lambda *a, **kw: compose)
        monkeypatch.setattr("swarm.nodes.inspect_nodes", lambda: SAMPLE_NODES)
        result = get_service_node("fake/compose.yml")
        assert result[0] == ("svc", "UNRESOLVED")
