import project_sandbox as ps


class FakeRunner(ps.Runner):
    def __init__(self, out):
        super().__init__(dry_run=False)
        self._out = out

    def capture(self, cmd, check=True):
        self.calls.append(list(cmd))
        return self._out


def test_ls_joins_registry_and_docker(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    ps.save_registry({"instances": {"demo": {"stack": "webapp", "ports": {"API_WEB": 8180}}}})
    out = (
        '{"Names":"api-svc_demo","State":"running","Status":"Up",'
        '"Labels":"project-sandbox.instance=demo,project-sandbox.stack=webapp"}\n'
    )
    rc = ps.cmd_ls(type("A", (), {}), FakeRunner(out))
    captured = capsys.readouterr().out
    assert rc == 0
    assert "demo" in captured and "webapp" in captured and "API_WEB:8180" in captured


def test_ls_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    ps.cmd_ls(type("A", (), {}), FakeRunner(""))
    assert "no sandboxes" in capsys.readouterr().out


def test_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    ps.save_registry(
        {
            "instances": {
                "demo": {
                    "stack": "webapp",
                    "project_name": "webapp-demo",
                    "network": "app_network_demo",
                    "ports": {"API_WEB": 8180},
                    "worktrees": ["/wt/api"],
                }
            }
        }
    )
    ps.cmd_status(type("A", (), {"instance": "demo"}), ps.Runner(dry_run=True))
    out = capsys.readouterr().out
    assert "webapp-demo" in out and "API_WEB" in out
