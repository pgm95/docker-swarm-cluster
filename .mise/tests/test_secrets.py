"""Tests for swarm.secrets — secret validation and versioned creation."""

import pytest

from swarm import SecretError, ValidationError
from swarm.secrets import (
    create_versioned_secrets,
    parse_versioned_names,
    validate_config_files,
    validate_required_secrets,
)


class TestParseVersionedNames:
    def test_extracts_names(self, tmp_path):
        yml = tmp_path / "secrets.yml"
        yml.write_text(
            "authelia_jwt_secret:\n"
            "    name: authelia_jwt_secret_${DEPLOY_VERSION}\n"
            "    external: true\n"
            "lldap_ldap_user_pass:\n"
            "    name: lldap_ldap_user_pass_${DEPLOY_VERSION}\n"
            "    external: true\n"
        )
        result = parse_versioned_names(yml)
        assert result == {"authelia_jwt_secret", "lldap_ldap_user_pass"}

    def test_no_deploy_version(self, tmp_path):
        yml = tmp_path / "secrets.yml"
        yml.write_text("some_secret:\n    external: true\n")
        assert parse_versioned_names(yml) == set()

    def test_no_file(self, tmp_path):
        assert parse_versioned_names(tmp_path / "nonexistent.yml") == set()

    def test_single_secret(self, tmp_path):
        yml = tmp_path / "secrets.yml"
        yml.write_text("db_pass:\n    name: db_pass_${DEPLOY_VERSION}\n    external: true\n")
        assert parse_versioned_names(yml) == {"db_pass"}


class TestValidateRequiredSecrets:
    def test_all_present(self, tmp_stack):
        stack = tmp_stack(
            secrets_yml="db_pass:\n    name: db_pass_${DEPLOY_VERSION}\n    external: true\n",
        )
        env = {"DB_PASS": "secret123"}
        validate_required_secrets(stack, env=env)

    def test_missing_raises(self, tmp_stack):
        stack = tmp_stack(
            secrets_yml="db_pass:\n    name: db_pass_${DEPLOY_VERSION}\n    external: true\n",
        )
        with pytest.raises(SecretError, match="DB_PASS"):
            validate_required_secrets(stack, env={})

    def test_no_secrets_yml(self, tmp_stack):
        stack = tmp_stack()
        validate_required_secrets(stack, env={})

    def test_no_versioned_secrets(self, tmp_stack):
        stack = tmp_stack(
            secrets_yml="some_secret:\n    external: true\n",
        )
        validate_required_secrets(stack, env={})


class TestCreateVersionedSecrets:
    def test_creates_secrets(self, tmp_stack, monkeypatch):
        stack = tmp_stack(
            secrets_yml="db_pass:\n    name: db_pass_${DEPLOY_VERSION}\n    external: true\n",
            secrets_env="placeholder",
        )
        monkeypatch.setattr(
            "swarm.secrets.sops_decrypt",
            lambda f: [("DB_PASS", "secret123"), ("OTHER_KEY", "ignored")],
        )
        created_secrets = []
        monkeypatch.setattr("swarm.secrets.secret_exists", lambda name: False)
        monkeypatch.setattr("swarm.secrets.secret_create", lambda name, val: created_secrets.append((name, val)))

        result = create_versioned_secrets(stack, "abc_123", env={"GLOBAL_SECRETS": ""})
        assert result["created"] == 1
        assert result["filtered"] == 1
        assert created_secrets[0] == ("db_pass_abc_123", "secret123")

    def test_skips_existing(self, tmp_stack, monkeypatch):
        stack = tmp_stack(
            secrets_yml="db_pass:\n    name: db_pass_${DEPLOY_VERSION}\n    external: true\n",
            secrets_env="placeholder",
        )
        monkeypatch.setattr("swarm.secrets.sops_decrypt", lambda f: [("DB_PASS", "val")])
        monkeypatch.setattr("swarm.secrets.secret_exists", lambda name: True)
        monkeypatch.setattr("swarm.secrets.secret_create", lambda name, val: None)

        result = create_versioned_secrets(stack, "abc_123", env={"GLOBAL_SECRETS": ""})
        assert result["skipped"] == 1
        assert result["created"] == 0

    def test_filters_unneeded(self, tmp_stack, monkeypatch):
        stack = tmp_stack(
            secrets_yml="db_pass:\n    name: db_pass_${DEPLOY_VERSION}\n    external: true\n",
            secrets_env="placeholder",
        )
        monkeypatch.setattr("swarm.secrets.sops_decrypt", lambda f: [("UNRELATED", "val")])
        monkeypatch.setattr("swarm.secrets.secret_exists", lambda name: False)
        monkeypatch.setattr("swarm.secrets.secret_create", lambda name, val: None)

        result = create_versioned_secrets(stack, "abc_123", env={"GLOBAL_SECRETS": ""})
        assert result["filtered"] == 1
        assert result["created"] == 0

    def test_no_secrets_yml(self, tmp_stack, monkeypatch):
        stack = tmp_stack()
        result = create_versioned_secrets(stack, "abc_123", env={"GLOBAL_SECRETS": ""})
        assert result == {"created": 0, "skipped": 0, "filtered": 0}

    def test_b64_handled(self, tmp_stack, monkeypatch):
        """_B64 suffix should already be resolved by _sops.sops_decrypt."""
        stack = tmp_stack(
            secrets_yml="oidc_key:\n    name: oidc_key_${DEPLOY_VERSION}\n    external: true\n",
            secrets_env="placeholder",
        )
        # sops_decrypt returns already-decoded values (key without _B64 suffix)
        monkeypatch.setattr("swarm.secrets.sops_decrypt", lambda f: [("OIDC_KEY", "decoded-pem")])
        created = []
        monkeypatch.setattr("swarm.secrets.secret_exists", lambda name: False)
        monkeypatch.setattr("swarm.secrets.secret_create", lambda name, val: created.append((name, val)))

        create_versioned_secrets(stack, "v1", env={"GLOBAL_SECRETS": ""})
        assert created[0] == ("oidc_key_v1", "decoded-pem")


class TestValidateConfigFiles:
    def test_all_exist(self, tmp_stack):
        stack = tmp_stack(
            configs_yml="my_config:\n    file: config/app.yml\n",
            config_files={"app.yml": "key: value"},
        )
        validate_config_files(stack)

    def test_missing_raises(self, tmp_stack):
        stack = tmp_stack(
            configs_yml="my_config:\n    file: config/missing.yml\n",
        )
        with pytest.raises(ValidationError, match="missing.yml"):
            validate_config_files(stack)

    def test_no_configs_yml(self, tmp_stack):
        stack = tmp_stack()
        validate_config_files(stack)

    def test_multiple_missing(self, tmp_stack):
        stack = tmp_stack(
            configs_yml="c1:\n    file: config/a.yml\nc2:\n    file: config/b.yml\n",
        )
        with pytest.raises(ValidationError) as exc_info:
            validate_config_files(stack)
        assert "a.yml" in str(exc_info.value)
        assert "b.yml" in str(exc_info.value)
