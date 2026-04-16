"""Tests for swarm.networks — overlay network discovery and initialization."""


from conftest import make_completed
from swarm.networks import get_infra_networks, init_networks


class TestGetInfraNetworks:
    def test_discovers_from_compose(self, tmp_path, monkeypatch):
        infra = tmp_path / "stacks" / "infra"
        socket_dir = infra / "00_socket"
        socket_dir.mkdir(parents=True)
        (socket_dir / "compose.yml").write_text(
            "networks:\n"
            "  infra_socket:\n"
            "    external: true\n"
        )
        metrics_dir = infra / "40_metrics"
        metrics_dir.mkdir()
        (metrics_dir / "compose.yml").write_text(
            "networks:\n"
            "  infra_metrics:\n"
            "    external: true\n"
            "  infra_gw-internal:\n"
            "    external: true\n"
        )
        monkeypatch.chdir(tmp_path)
        result = get_infra_networks()
        assert result == ["infra_gw-internal", "infra_metrics", "infra_socket"]

    def test_deduplicates(self, tmp_path, monkeypatch):
        infra = tmp_path / "stacks" / "infra"
        for name in ["00_socket", "40_metrics"]:
            d = infra / name
            d.mkdir(parents=True)
            (d / "compose.yml").write_text(
                "networks:\n"
                "  infra_socket:\n"
                "    external: true\n"
            )
        monkeypatch.chdir(tmp_path)
        result = get_infra_networks()
        assert result == ["infra_socket"]

    def test_ignores_non_external(self, tmp_path, monkeypatch):
        infra = tmp_path / "stacks" / "infra" / "00_socket"
        infra.mkdir(parents=True)
        (infra / "compose.yml").write_text(
            "networks:\n"
            "  infra_socket:\n"
            "    external: true\n"
            "  default:\n"
            "    driver: overlay\n"
        )
        monkeypatch.chdir(tmp_path)
        result = get_infra_networks()
        assert result == ["infra_socket"]

    def test_empty_no_stacks(self, tmp_path, monkeypatch):
        (tmp_path / "stacks" / "infra").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        assert get_infra_networks() == []


class TestInitNetworks:
    def test_creates_new(self, monkeypatch):
        monkeypatch.setattr("swarm.networks.get_infra_networks", lambda: ["infra_socket", "infra_metrics"])

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
        assert set(created) == {"infra_socket", "infra_metrics"}

    def test_skips_existing(self, monkeypatch):
        monkeypatch.setattr("swarm.networks.get_infra_networks", lambda: ["infra_socket"])

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
        monkeypatch.setattr("swarm.networks.get_infra_networks", lambda: ["infra_socket"])

        create_args = []

        def fake_docker(*args, check=True):
            if args[0] == "network" and args[1] == "inspect":
                return make_completed(returncode=1)
            if args[0] == "network" and args[1] == "create":
                create_args.append(args)
                return make_completed()
            return make_completed()

        monkeypatch.setattr("swarm.networks.docker_run", fake_docker)
        init_networks(internal_networks={"infra_socket"})
        assert "--internal" in create_args[0]

    def test_non_internal(self, monkeypatch):
        monkeypatch.setattr("swarm.networks.get_infra_networks", lambda: ["infra_metrics"])

        create_args = []

        def fake_docker(*args, check=True):
            if args[0] == "network" and args[1] == "inspect":
                return make_completed(returncode=1)
            if args[0] == "network" and args[1] == "create":
                create_args.append(args)
                return make_completed()
            return make_completed()

        monkeypatch.setattr("swarm.networks.docker_run", fake_docker)
        init_networks(internal_networks={"infra_socket"})
        assert "--internal" not in create_args[0]

    def test_mtu_from_env(self, monkeypatch):
        monkeypatch.setattr("swarm.networks.get_infra_networks", lambda: ["infra_metrics"])
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
        monkeypatch.setattr("swarm.networks.get_infra_networks", lambda: ["infra_metrics"])
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
