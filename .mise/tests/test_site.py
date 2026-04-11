"""Tests for swarm.site — site-wide orchestration."""


from swarm.site import deploy_apps, deploy_infra, reset


class TestDeployInfra:
    def test_deploys_in_order(self, tmp_path, monkeypatch):
        for name in ["00_socket", "10_postgres"]:
            (tmp_path / name).mkdir()
            (tmp_path / name / "compose.yml").write_text("services: {}")

        monkeypatch.setattr("swarm.site.find_stacks", lambda ns: sorted(tmp_path.iterdir()))

        deployed = []
        monkeypatch.setattr("swarm.site._mise_run", lambda task, *a: (deployed.append(a[0] if a else task), True)[1])

        result = deploy_infra()
        assert result == 0
        assert deployed == [
            str(tmp_path / "00_socket"),
            str(tmp_path / "10_postgres"),
        ]

    def test_no_registry_auth(self, tmp_path, monkeypatch):
        (tmp_path / "50_registry").mkdir()
        (tmp_path / "50_registry" / "compose.yml").write_text("services: {}")

        monkeypatch.setattr("swarm.site.find_stacks", lambda ns: [tmp_path / "50_registry"])

        tasks_run = []
        monkeypatch.setattr("swarm.site._mise_run", lambda task, *a: (tasks_run.append(task), True)[1])

        deploy_infra()
        assert "registry:auth" not in tasks_run
        assert "site:registry-auth" not in tasks_run

    def test_collects_failures(self, tmp_path, monkeypatch):
        (tmp_path / "00_socket").mkdir()
        (tmp_path / "10_postgres").mkdir()

        monkeypatch.setattr("swarm.site.find_stacks", lambda ns: sorted(tmp_path.iterdir()))

        call_count = 0

        def fail_first(task, *a):
            nonlocal call_count
            call_count += 1
            return call_count != 1

        monkeypatch.setattr("swarm.site._mise_run", fail_first)

        result = deploy_infra()
        assert result == 1  # at least one failure

    def test_empty_namespace(self, monkeypatch):
        monkeypatch.setattr("swarm.site.find_stacks", lambda ns: [])
        result = deploy_infra()
        assert result == 0


class TestDeployApps:
    def test_skips_nodeploy(self, tmp_path, monkeypatch):
        app1 = tmp_path / "mealie"
        app1.mkdir()
        (app1 / "compose.yml").write_text("services: {}")

        app2 = tmp_path / "jellyfin"
        app2.mkdir()
        (app2 / "compose.yml").write_text("services: {}")
        (app2 / ".nodeploy").write_text("")

        monkeypatch.setattr("swarm.site.find_stacks", lambda ns: sorted(tmp_path.iterdir()))
        monkeypatch.setattr("time.sleep", lambda s: None)

        deployed = []
        monkeypatch.setattr("swarm.site._mise_run", lambda task, *a: (deployed.append(a), True)[1])

        result = deploy_apps()
        assert result == 0
        # Only mealie deployed, jellyfin skipped
        assert len(deployed) == 1

    def test_collects_failures(self, tmp_path, monkeypatch):
        (tmp_path / "app1").mkdir()
        (tmp_path / "app1" / "compose.yml").write_text("services: {}")

        monkeypatch.setattr("swarm.site.find_stacks", lambda ns: [tmp_path / "app1"])
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setattr("swarm.site._mise_run", lambda task, *a: False)

        result = deploy_apps()
        assert result == 1


class TestReset:
    def test_removes_apps_then_infra_reversed(self, tmp_path, monkeypatch):
        apps = tmp_path / "apps"
        infra = tmp_path / "infra"
        for d in [apps, infra]:
            d.mkdir()
        (apps / "mealie").mkdir()
        (infra / "00_socket").mkdir()
        (infra / "10_postgres").mkdir()

        def fake_find(ns, reverse=False):
            if "apps" in str(ns):
                return [apps / "mealie"]
            dirs = sorted(infra.iterdir(), reverse=reverse)
            return dirs

        monkeypatch.setattr("swarm.site.find_stacks", fake_find)
        monkeypatch.setattr("swarm.site.get_infra_networks", lambda: ["infra_socket"])

        from conftest import make_completed
        monkeypatch.setattr("swarm.site.docker_run", lambda *a, **kw: make_completed())

        removed = []
        monkeypatch.setattr("swarm.site._mise_run", lambda task, *a: (removed.append((task, a)), True)[1])

        reset()
        # Apps removed first, then infra in reverse
        tasks = [t for t, _ in removed]
        assert all(t == "swarm:remove" for t in tasks)
        paths = [a[0] for _, a in removed]
        assert "mealie" in str(paths[0])
        assert "10_postgres" in str(paths[1])  # reverse order
        assert "00_socket" in str(paths[2])

    def test_volumes_flag_prunes_nodes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("swarm.site.find_stacks", lambda ns, **kw: [])
        monkeypatch.setattr("swarm.site.get_infra_networks", lambda: [])

        from conftest import make_completed
        monkeypatch.setattr("swarm.site.docker_run", lambda *a, **kw: make_completed())
        monkeypatch.setattr("swarm.site.get_swarm_nodes", lambda: [{"hostname": "node1"}])

        ssh_calls = []
        monkeypatch.setattr(
            "swarm.site.ssh_node",
            lambda h, c, **kw: (ssh_calls.append((h, c)), make_completed(stdout="abc123\n"))[1],
        )

        reset(volumes=True)
        assert len(ssh_calls) == 1
        assert "volume prune" in ssh_calls[0][1]

    def test_no_volumes_skips_prune(self, tmp_path, monkeypatch):
        monkeypatch.setattr("swarm.site.find_stacks", lambda ns, **kw: [])
        monkeypatch.setattr("swarm.site.get_infra_networks", lambda: [])

        from conftest import make_completed
        monkeypatch.setattr("swarm.site.docker_run", lambda *a, **kw: make_completed())

        ssh_calls = []
        monkeypatch.setattr(
            "swarm.site.ssh_node",
            lambda h, c, **kw: (ssh_calls.append(h), make_completed())[1],
        )

        reset(volumes=False)
        assert len(ssh_calls) == 0

    def test_removes_networks(self, monkeypatch):
        monkeypatch.setattr("swarm.site.find_stacks", lambda ns, **kw: [])
        monkeypatch.setattr("swarm.site.get_infra_networks", lambda: ["infra_socket", "infra_gw-internal"])

        removed_nets = []

        from conftest import make_completed

        def fake_docker(*args, **kwargs):
            if args[0] == "network":
                removed_nets.append(args[2])
            return make_completed()

        monkeypatch.setattr("swarm.site.docker_run", fake_docker)

        reset()
        assert removed_nets == ["infra_socket", "infra_gw-internal"]
