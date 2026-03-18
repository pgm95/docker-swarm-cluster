"""Tests for swarm.validate — compose validation and bind mount checks."""

import json


from conftest import make_completed
from swarm.validate import _sed_transforms, check_bind_mounts, extract_bind_mounts, validate_compose


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


class TestSedTransforms:
    def test_removes_root_name(self):
        config = "name: mystack\nservices:\n  web:\n    image: nginx"
        result = _sed_transforms(config)
        assert "name:" not in result.splitlines()[0]
        assert "services:" in result

    def test_unquotes_ports(self):
        config = '    published: "443"'
        result = _sed_transforms(config)
        assert 'published: 443' in result

    def test_preserves_indented_name(self):
        config = "services:\n  web:\n    container_name: myapp"
        result = _sed_transforms(config)
        assert "container_name: myapp" in result

    def test_multiple_ports(self):
        config = '    published: "80"\n    published: "443"'
        result = _sed_transforms(config)
        assert '"' not in result


class TestValidateCompose:
    def test_valid(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.validate.compose_config",
            lambda *a: "name: test\nservices:\n  web:\n    image: nginx\n",
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: make_completed(),
        )
        from pathlib import Path
        valid, err = validate_compose(Path("stacks/infra/00_socket/compose.yml"))
        assert valid is True
        assert err == ""

    def test_invalid_compose_config(self, monkeypatch):
        def fail(*a):
            raise Exception("bad yaml")
        monkeypatch.setattr("swarm.validate.compose_config", fail)
        from pathlib import Path
        valid, err = validate_compose(Path("stacks/infra/00_socket/compose.yml"))
        assert valid is False
        assert "bad yaml" in err

    def test_invalid_stack_config(self, monkeypatch):
        monkeypatch.setattr(
            "swarm.validate.compose_config",
            lambda *a: "services:\n  web:\n    image: nginx\n",
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: make_completed(returncode=1, stderr="invalid config"),
        )
        from pathlib import Path
        valid, err = validate_compose(Path("stacks/infra/00_socket/compose.yml"))
        assert valid is False
        assert "invalid config" in err


class TestCheckBindMounts:
    def test_finds_missing_paths(self, monkeypatch):
        compose_json = json.dumps({
            "services": {
                "web": {
                    "volumes": [{"type": "bind", "source": "/mnt/data", "target": "/data"}],
                    "deploy": {"placement": {"constraints": ["node.labels.type == vm"]}},
                },
            },
        })
        monkeypatch.setattr("swarm.validate.compose_config", lambda *a: compose_json)
        monkeypatch.setattr("swarm.validate.get_service_node", lambda f: [("web", "swarm-vm")])
        monkeypatch.setattr(
            "swarm.validate.ssh_node",
            lambda h, c, **kw: make_completed(stdout="MISSING /mnt/data"),
        )
        from pathlib import Path
        results = check_bind_mounts(Path("stacks/apps/test/compose.yml"))
        assert len(results) == 1
        assert results[0]["status"] == "missing"
        assert results[0]["path"] == "/mnt/data"

    def test_ok_paths(self, monkeypatch):
        compose_json = json.dumps({
            "services": {
                "web": {
                    "volumes": [{"type": "bind", "source": "/mnt/data", "target": "/data"}],
                },
            },
        })
        monkeypatch.setattr("swarm.validate.compose_config", lambda *a: compose_json)
        monkeypatch.setattr("swarm.validate.get_service_node", lambda f: [("web", "swarm-vm")])
        monkeypatch.setattr(
            "swarm.validate.ssh_node",
            lambda h, c, **kw: make_completed(stdout="drwxr-xr-x root:root /mnt/data"),
        )
        from pathlib import Path
        results = check_bind_mounts(Path("stacks/apps/test/compose.yml"))
        assert len(results) == 1
        assert results[0]["status"] == "ok"

    def test_no_bind_mounts(self, monkeypatch):
        compose_json = json.dumps({"services": {"web": {"image": "nginx"}}})
        monkeypatch.setattr("swarm.validate.compose_config", lambda *a: compose_json)
        from pathlib import Path
        results = check_bind_mounts(Path("stacks/apps/test/compose.yml"))
        assert results == []

    def test_unreachable_node(self, monkeypatch):
        compose_json = json.dumps({
            "services": {
                "web": {
                    "volumes": [{"type": "bind", "source": "/mnt/data", "target": "/data"}],
                },
            },
        })
        monkeypatch.setattr("swarm.validate.compose_config", lambda *a: compose_json)
        monkeypatch.setattr("swarm.validate.get_service_node", lambda f: [("web", "UNRESOLVED")])
        from pathlib import Path
        results = check_bind_mounts(Path("stacks/apps/test/compose.yml"))
        assert results[0]["status"] == "unreachable"
