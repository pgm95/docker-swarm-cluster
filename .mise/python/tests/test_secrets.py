"""Tests for swarm.secrets — compose-JSON-driven secret/config validation."""

import pytest

from swarm import SecretError, SwarmError, ValidationError
from swarm.secrets import (
    all_secrets_files,
    create_versioned_secrets,
    main,
    referenced_config_files,
    required_versioned_secrets,
    secrets_file_for,
    secrets_targets,
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
        monkeypatch.setattr("swarm.secrets.secret_list", list)
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
        monkeypatch.setattr("swarm.secrets.secret_list", list)
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
        monkeypatch.setattr("swarm.secrets.secret_list", list)
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
        """secrets.sops.yaml may have keys the compose doesn't reference; ignore them."""
        compose = _compose(secrets={"db_pass": {"name": "db_pass_v1", "external": True}})
        monkeypatch.setattr("swarm.secrets.secret_list", list)
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

    def test_creation_order_is_sorted_within_each_source(self, monkeypatch):
        """Stack-local names first (sorted), then env-sourced names (sorted),
        regardless of the order keys appear in the secrets file."""
        compose = _compose(secrets={
            n: {"name": f"{n}_v1", "external": True} for n in ("zeta", "alpha", "global_b", "global_a")
        })
        monkeypatch.setattr("swarm.secrets.secret_list", list)
        created = []
        monkeypatch.setattr("swarm.secrets.secret_create", lambda n, v: created.append(n))
        create_versioned_secrets(
            compose, "v1",
            stack_secrets=[("ZETA", "1"), ("ALPHA", "2")],
            env={"GLOBAL_B": "3", "GLOBAL_A": "4"},
        )
        assert created == ["alpha_v1", "zeta_v1", "global_a_v1", "global_b_v1"]


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


@pytest.fixture
def secrets_tree(tmp_path, stacks_tree, monkeypatch):
    """Global SOPS files next to the shared stacks tree; two stacks carry a secrets file."""
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    for stem in ("shared", "dev", "prod"):
        (secrets_dir / f"{stem}.sops.yaml").write_text("K: v\n")
    (secrets_dir / "notes.txt").write_text("not a sops file\n")
    (stacks_tree / "infra/10_postgres/secrets.sops.yaml").write_text("K: v\n")
    (stacks_tree / "apps/mealie/secrets.sops.yaml").write_text("K: v\n")
    monkeypatch.setenv("PROJECT_SECRETS_DIR", str(secrets_dir))
    return tmp_path


class TestSecretsTargets:
    def test_global_target_resolves(self, secrets_tree):
        assert secrets_file_for("shared") == secrets_tree / ".secrets/shared.sops.yaml"

    def test_stack_target_by_bare_name(self, secrets_tree):
        p = secrets_file_for("metrics")
        assert p == secrets_tree / "stacks/infra/40_metrics/secrets.sops.yaml"
        assert not p.exists()  # resolution does not require the file to exist

    def test_stack_target_by_dir_name(self, secrets_tree):
        assert secrets_file_for("10_postgres") == secrets_tree / "stacks/infra/10_postgres/secrets.sops.yaml"

    def test_unknown_target_raises(self, secrets_tree):
        with pytest.raises(SwarmError, match="Stack not found"):
            secrets_file_for("nope")

    def test_targets_globals_then_stacks(self, secrets_tree):
        assert secrets_targets() == ["dev", "prod", "shared", "mealie", "tools", "postgres", "metrics"]

    def test_all_files_only_existing(self, secrets_tree):
        rel = [str(p.relative_to(secrets_tree)) for p in all_secrets_files()]
        assert rel == [
            ".secrets/dev.sops.yaml",
            ".secrets/prod.sops.yaml",
            ".secrets/shared.sops.yaml",
            "stacks/apps/mealie/secrets.sops.yaml",
            "stacks/infra/10_postgres/secrets.sops.yaml",
        ]

    def test_missing_secrets_dir(self, secrets_tree, monkeypatch):
        monkeypatch.setenv("PROJECT_SECRETS_DIR", str(secrets_tree / "absent"))
        assert secrets_targets() == ["mealie", "tools", "postgres", "metrics"]


class TestPathCli:
    def _run(self, monkeypatch, capsys, *argv):
        monkeypatch.setattr("sys.argv", ["swarm.secrets", "path", *argv])
        rc = main()
        return rc, capsys.readouterr()

    def test_single_target(self, secrets_tree, monkeypatch, capsys):
        rc, out = self._run(monkeypatch, capsys, "mealie")
        assert rc == 0
        assert out.out.strip() == str(secrets_tree / "stacks/apps/mealie/secrets.sops.yaml")

    def test_all(self, secrets_tree, monkeypatch, capsys):
        rc, out = self._run(monkeypatch, capsys, "--all")
        assert rc == 0
        assert len(out.out.splitlines()) == 5

    def test_targets(self, secrets_tree, monkeypatch, capsys):
        rc, out = self._run(monkeypatch, capsys, "--targets")
        assert rc == 0
        assert out.out.split() == ["dev", "prod", "shared", "mealie", "tools", "postgres", "metrics"]

    def test_no_args_is_usage_error(self, secrets_tree, monkeypatch, capsys):
        rc, out = self._run(monkeypatch, capsys)
        assert rc == 1
        assert out.out == ""

    def test_unknown_target_nonzero(self, secrets_tree, monkeypatch, capsys):
        rc, out = self._run(monkeypatch, capsys, "nope")
        assert rc != 0
        assert out.out == ""
