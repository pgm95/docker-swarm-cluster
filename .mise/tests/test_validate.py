"""Tests for swarm.validate — compose validation and bind mount checks."""

from swarm.validate import _sed_transforms, extract_bind_mounts


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
