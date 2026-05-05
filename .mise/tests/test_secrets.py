"""Tests for swarm.secrets — compose-JSON-driven secret/config validation."""

import pytest

from swarm import SecretError, ValidationError
from swarm.secrets import (
    create_versioned_secrets,
    referenced_config_files,
    required_versioned_secrets,
    validate_config_files,
    validate_required_secrets,
)


def _compose(secrets: dict | None = None, configs: dict | None = None) -> dict:
    """Build a fake `docker compose config --format json` payload."""
    return {"services": {}, "secrets": secrets or {}, "configs": configs or {}}


class TestRequiredVersionedSecrets:
    def test_picks_up_versioned_suffix(self):
        compose = _compose(secrets={
            "db_pass": {"name": "db_pass_abc1234_1700000000", "external": True},
            "static": {"name": "static_unrelated", "external": True},
            "lldap_jwt": {"name": "lldap_jwt_abc1234_1700000000", "external": True},
        })
        assert required_versioned_secrets(compose, "abc1234_1700000000") == {"db_pass", "lldap_jwt"}

    def test_no_secrets_block(self):
        assert required_versioned_secrets({"services": {}}, "v1") == set()

    def test_none_match_version(self):
        compose = _compose(secrets={"db_pass": {"name": "db_pass_other_version", "external": True}})
        assert required_versioned_secrets(compose, "abc_123") == set()

    def test_secrets_value_can_be_none(self):
        # docker compose config sometimes emits {} for an external-only entry
        compose = _compose(secrets={"x": None})
        assert required_versioned_secrets(compose, "v1") == set()


class TestReferencedConfigFiles:
    def test_collects_file_paths(self):
        compose = _compose(configs={
            "conf_a": {"file": "/abs/path/a.conf"},
            "conf_b": {"file": "config/b.yml"},
            "no_file": {"name": "external_thing", "external": True},
        })
        result = referenced_config_files(compose)
        assert sorted(result) == ["/abs/path/a.conf", "config/b.yml"]

    def test_no_configs_block(self):
        assert referenced_config_files({}) == []


class TestValidateRequiredSecrets:
    def test_all_present_in_stack_secrets(self):
        compose = _compose(secrets={"db_pass": {"name": "db_pass_v1", "external": True}})
        validate_required_secrets(
            compose, "v1",
            stack_secrets=[("DB_PASS", "secret")],
            env={},
        )

    def test_all_present_in_env(self):
        compose = _compose(secrets={"global_token": {"name": "global_token_v1", "external": True}})
        validate_required_secrets(
            compose, "v1",
            stack_secrets=[],
            env={"GLOBAL_TOKEN": "abc"},
        )

    def test_missing_raises(self):
        compose = _compose(secrets={"db_pass": {"name": "db_pass_v1", "external": True}})
        with pytest.raises(SecretError, match="DB_PASS"):
            validate_required_secrets(compose, "v1", stack_secrets=[], env={})

    def test_no_versioned_secrets_is_noop(self):
        validate_required_secrets({"services": {}}, "v1", stack_secrets=[], env={})


class TestCreateVersionedSecrets:
    def test_creates_from_stack_secrets(self, monkeypatch):
        compose = _compose(secrets={"db_pass": {"name": "db_pass_v1", "external": True}})
        monkeypatch.setattr("swarm.secrets.secret_list", lambda: [])
        created = []
        monkeypatch.setattr("swarm.secrets.secret_create", lambda n, v: created.append((n, v)))
        result = create_versioned_secrets(
            compose, "v1",
            stack_secrets=[("DB_PASS", "secret")],
            env={},
        )
        assert result["created"] == 1
        assert created == [("db_pass_v1", "secret")]

    def test_creates_from_env(self, monkeypatch):
        compose = _compose(secrets={"global_cf": {"name": "global_cf_v1", "external": True}})
        monkeypatch.setattr("swarm.secrets.secret_list", lambda: [])
        created = []
        monkeypatch.setattr("swarm.secrets.secret_create", lambda n, v: created.append((n, v)))
        result = create_versioned_secrets(
            compose, "v1",
            stack_secrets=[],
            env={"GLOBAL_CF": "tokenval"},
        )
        assert result["created"] == 1
        assert created == [("global_cf_v1", "tokenval")]

    def test_stack_secrets_take_precedence(self, monkeypatch):
        """When the same name is in both stack secrets and env, stack wins."""
        compose = _compose(secrets={"db_pass": {"name": "db_pass_v1", "external": True}})
        monkeypatch.setattr("swarm.secrets.secret_list", lambda: [])
        created = []
        monkeypatch.setattr("swarm.secrets.secret_create", lambda n, v: created.append((n, v)))
        create_versioned_secrets(
            compose, "v1",
            stack_secrets=[("DB_PASS", "from-stack")],
            env={"DB_PASS": "from-env"},
        )
        assert created == [("db_pass_v1", "from-stack")]

    def test_skips_existing(self, monkeypatch):
        compose = _compose(secrets={"db_pass": {"name": "db_pass_v1", "external": True}})
        monkeypatch.setattr("swarm.secrets.secret_list", lambda: ["db_pass_v1"])
        monkeypatch.setattr("swarm.secrets.secret_create", lambda n, v: None)
        result = create_versioned_secrets(
            compose, "v1", stack_secrets=[("DB_PASS", "x")], env={},
        )
        assert result["skipped"] == 1
        assert result["created"] == 0

    def test_ignores_unneeded_stack_keys(self, monkeypatch):
        """secrets.env may have keys the compose doesn't reference; ignore them."""
        compose = _compose(secrets={"db_pass": {"name": "db_pass_v1", "external": True}})
        monkeypatch.setattr("swarm.secrets.secret_list", lambda: [])
        created = []
        monkeypatch.setattr("swarm.secrets.secret_create", lambda n, v: created.append((n, v)))
        create_versioned_secrets(
            compose, "v1",
            stack_secrets=[("DB_PASS", "v"), ("UNRELATED", "x")],
            env={},
        )
        assert created == [("db_pass_v1", "v")]

    def test_no_versioned_secrets_returns_zero(self, monkeypatch):
        result = create_versioned_secrets(_compose(), "v1", stack_secrets=[], env={})
        assert result == {"created": 0, "skipped": 0}


class TestValidateConfigFiles:
    def test_all_exist(self, tmp_path):
        f = tmp_path / "app.yml"
        f.write_text("key: value")
        compose = _compose(configs={"my_config": {"file": str(f)}})
        validate_config_files(compose)

    def test_missing_raises(self, tmp_path):
        compose = _compose(configs={"my_config": {"file": str(tmp_path / "missing.yml")}})
        with pytest.raises(ValidationError, match="missing.yml"):
            validate_config_files(compose)

    def test_no_configs_block(self):
        validate_config_files({"services": {}})

    def test_multiple_missing(self, tmp_path):
        compose = _compose(configs={
            "c1": {"file": str(tmp_path / "a.yml")},
            "c2": {"file": str(tmp_path / "b.yml")},
        })
        with pytest.raises(ValidationError) as exc_info:
            validate_config_files(compose)
        assert "a.yml" in str(exc_info.value)
        assert "b.yml" in str(exc_info.value)
