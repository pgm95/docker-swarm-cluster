"""Tests for swarm.networks — overlay network discovery and initialization."""

from conftest import make_completed
from swarm.networks import get_external_networks, init_networks


def _render(name_specs: dict[str, dict]) -> dict:
    """Build a fake parsed compose JSON dict (what compose_json returns)."""
    return {"networks": name_specs, "services": {}}


class TestGetExternalNetworks:
    """get_external_networks walks every stack's rendered compose JSON
    looking for `networks.<name>` entries with `external: true`."""

    def test_discovers_across_namespaces(self, tmp_path, monkeypatch):
        # Layout: <stacks>/<ns>/<stack>/compose.yml
        # The shared_b reference appears in both ns-alpha and ns-beta — verifies
        # cross-stack network references dedupe naturally.
        for ns, stack in [
            ("ns-alpha", "00_one"),
            ("ns-alpha", "10_two"),
            ("ns-beta", "first"),
            ("ns-gamma", "thing"),
        ]:
            d = tmp_path / "stacks" / ns / stack
            d.mkdir(parents=True)
            (d / "compose.yml").write_text("services: {}\n")

        monkeypatch.setenv("SWARM_STACKS_DIR", str(tmp_path / "stacks"))

        # Stub compose_json to return a render keyed off stack-dir name
        renders = {
            "00_one": {"shared_a": {"external": True}},
            "10_two": {"shared_b": {"external": True}},
            "first": {"shared_b": {"external": True}},
            "thing": {
                "gamma_bus": {"external": True},
                "default": {"driver": "overlay"},  # not external, ignored
            },
        }

        def fake_compose_json(compose_path, *args):
            stack_dir_name = compose_path.parent.name
            return _render(renders.get(stack_dir_name, {}))

        monkeypatch.setattr("swarm.networks.compose_json", fake_compose_json)

        result = get_external_networks()
        assert result == ["gamma_bus", "shared_a", "shared_b"]

    def test_render_failure_is_skipped(self, tmp_path, monkeypatch):
        d = tmp_path / "stacks" / "fake-ns" / "fake-stack"
        d.mkdir(parents=True)
        (d / "compose.yml").write_text("services: {}\n")
        monkeypatch.setenv("SWARM_STACKS_DIR", str(tmp_path / "stacks"))

        def boom(*args, **kw):
            raise RuntimeError("compose blew up")

        monkeypatch.setattr("swarm.networks.compose_json", boom)
        # Failure is swallowed; no networks found
        assert get_external_networks() == []

    def test_no_namespaces(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_STACKS_DIR", str(tmp_path / "stacks"))
        assert get_external_networks() == []


class TestInitNetworks:
    def test_creates_new(self, monkeypatch):
        monkeypatch.setattr("swarm.networks.get_external_networks", lambda: ["net_alpha", "net_beta"])

        created = []

        def fake_docker(*args, check=True):
            if args[0] == "network" and args[1] == "inspect":
                return make_completed(returncode=1)
            if args[0] == "network" and args[1] == "create":
                created.append(args[-1])
                return make_completed()
            return make_completed()

        monkeypatch.setattr("swarm.networks.docker_run", fake_docker)
        init_networks()
        assert set(created) == {"net_alpha", "net_beta"}

    def test_skips_existing(self, monkeypatch):
        monkeypatch.setattr("swarm.networks.get_external_networks", lambda: ["net_alpha"])

        created = []

        def fake_docker(*args, check=True):
            if args[0] == "network" and args[1] == "inspect":
                return make_completed(returncode=0)  # exists
            if args[0] == "network" and args[1] == "create":
                created.append(args[-1])
                return make_completed()
            return make_completed()

        monkeypatch.setattr("swarm.networks.docker_run", fake_docker)
        init_networks()
        assert created == []

    def test_internal_flag(self, monkeypatch):
        monkeypatch.setattr("swarm.networks.get_external_networks", lambda: ["net_alpha"])

        create_args = []

        def fake_docker(*args, check=True):
            if args[0] == "network" and args[1] == "inspect":
                return make_completed(returncode=1)
            if args[0] == "network" and args[1] == "create":
                create_args.append(args)
                return make_completed()
            return make_completed()

        monkeypatch.setattr("swarm.networks.docker_run", fake_docker)
        init_networks(internal_networks={"net_alpha"})
        assert "--internal" in create_args[0]

    def test_non_internal(self, monkeypatch):
        monkeypatch.setattr("swarm.networks.get_external_networks", lambda: ["net_beta"])

        create_args = []

        def fake_docker(*args, check=True):
            if args[0] == "network" and args[1] == "inspect":
                return make_completed(returncode=1)
            if args[0] == "network" and args[1] == "create":
                create_args.append(args)
                return make_completed()
            return make_completed()

        monkeypatch.setattr("swarm.networks.docker_run", fake_docker)
        init_networks(internal_networks={"net_alpha"})
        assert "--internal" not in create_args[0]

    def test_mtu_from_env(self, monkeypatch):
        monkeypatch.setattr("swarm.networks.get_external_networks", lambda: ["net_beta"])
        monkeypatch.setenv("SWARM_OVERLAY_MTU", "1280")

        create_args = []

        def fake_docker(*args, check=True):
            if args[0] == "network" and args[1] == "inspect":
                return make_completed(returncode=1)
            if args[0] == "network" and args[1] == "create":
                create_args.append(args)
                return make_completed()
            return make_completed()

        monkeypatch.setattr("swarm.networks.docker_run", fake_docker)
        init_networks()
        assert "--opt" in create_args[0]
        opt_idx = create_args[0].index("--opt")
        assert create_args[0][opt_idx + 1] == "com.docker.network.driver.mtu=1280"

    def test_no_mtu_without_env(self, monkeypatch):
        monkeypatch.setattr("swarm.networks.get_external_networks", lambda: ["net_beta"])
        monkeypatch.delenv("SWARM_OVERLAY_MTU", raising=False)

        create_args = []

        def fake_docker(*args, check=True):
            if args[0] == "network" and args[1] == "inspect":
                return make_completed(returncode=1)
            if args[0] == "network" and args[1] == "create":
                create_args.append(args)
                return make_completed()
            return make_completed()

        monkeypatch.setattr("swarm.networks.docker_run", fake_docker)
        init_networks()
        assert "--opt" not in create_args[0]
