# tests/test_docker_helpers.py
import project_sandbox as ps


def test_compose_cmd_shape():
    cmd = ps.compose_cmd(
        "webapp-demo", ["/a/docker-compose.yml", "/s/api.override.yml"], ["up", "-d"]
    )
    assert cmd == [
        "docker",
        "compose",
        "-p",
        "webapp-demo",
        "-f",
        "/a/docker-compose.yml",
        "-f",
        "/s/api.override.yml",
        "up",
        "-d",
    ]


def test_clone_volume_commands():
    r = ps.Runner(dry_run=True)
    ps.clone_volume(r, "base", "webapp-demo-db")
    joined = [" ".join(c) for c in r.calls]
    assert any("volume create webapp-demo-db" in c for c in joined)
    assert any("cp -a" in c for c in joined)


def test_network_create_dry_run_emits_create():
    r = ps.Runner(dry_run=True)
    ps.network_create(r, "app_network_demo")
    assert ["docker", "network", "create", "app_network_demo"] in r.calls


def test_list_instances_parses_labels():
    class FakeRunner(ps.Runner):
        def __init__(self, out):
            super().__init__(dry_run=False)
            self._out = out

        def capture(self, cmd, check=True):
            self.calls.append(list(cmd))
            return self._out

    out = '{"Names":"api-web_demo","State":"running","Status":"Up 2m","Labels":"project-sandbox.instance=demo,project-sandbox.stack=webapp"}\n'
    inst = ps.list_instances(FakeRunner(out))
    assert "demo" in inst
    assert inst["demo"]["stack"] == "webapp"
    assert inst["demo"]["containers"][0]["name"] == "api-web_demo"
