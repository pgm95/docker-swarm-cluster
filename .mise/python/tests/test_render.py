"""Tests for swarm._render: the Swarm compose document renderer."""


class TestComposeConfigStdinPipe:
    def test_pipes_combined_yaml_via_stdin(self, mock_docker, tmp_path, monkeypatch):
        """compose_config concatenates anchors+compose and pipes via input=."""
        from swarm._render import _read_anchors_file, compose_config

        anchors = tmp_path / "anchors.yml"
        anchors.write_text("x-anchor: &a value\n")
        monkeypatch.setenv("SWARM_ANCHORS_FILE", str(anchors))
        # The path-keyed cache is fresh for new tmp_path values, but clear
        # to keep tests independent.
        _read_anchors_file.cache_clear()

        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yml"
        compose_file.write_text("services:\n  web:\n    image: nginx\n")

        mock_docker.set_response("compose", stdout="services:\n  web:\n    image: nginx\n")
        compose_config(compose_file)

        assert len(mock_docker.calls) == 1
        args = mock_docker.calls[0]
        assert args[0] == "compose"
        assert "-f" in args
        # The "-f" value is "-" meaning stdin
        assert args[args.index("-f") + 1] == "-"
        # The combined anchors+compose YAML was piped on stdin
        piped = mock_docker.inputs[0]
        assert "x-anchor: &a value" in piped
        assert "image: nginx" in piped

    def test_missing_anchors_file_renders_compose_alone(self, mock_docker, tmp_path, monkeypatch):
        """SWARM_ANCHORS_FILE pointing at a nonexistent file is not an error;
        the lib renders the stack's compose.yml as-is."""
        from swarm._render import _read_anchors_file, compose_config

        monkeypatch.setenv("SWARM_ANCHORS_FILE", str(tmp_path / "does-not-exist.yml"))
        _read_anchors_file.cache_clear()

        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yml"
        compose_file.write_text("services:\n  web:\n    image: nginx\n")

        mock_docker.set_response("compose", stdout="services: {}\n")
        compose_config(compose_file)

        piped = mock_docker.inputs[0]
        assert "image: nginx" in piped
        assert "x-anchor" not in piped
