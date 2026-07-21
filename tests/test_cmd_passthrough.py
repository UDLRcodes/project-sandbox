import project_sandbox as ps


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    ps.save_registry({"instances": {"demo": {"stack": "webapp", "project_name": "webapp-demo"}}})


def test_logs(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    r = ps.Runner(dry_run=True)
    ps.cmd_logs(type("A", (), {"instance": "demo", "service": "api-svc"}), r)
    assert ["docker", "compose", "-p", "webapp-demo", "logs", "-f", "api-svc"] in r.calls


def test_exec(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    r = ps.Runner(dry_run=True)
    ps.cmd_exec(
        type("A", (), {"instance": "demo", "service": "api-svc", "cmd": ["--", "php", "-v"]}), r
    )
    assert ["docker", "compose", "-p", "webapp-demo", "exec", "api-svc", "php", "-v"] in r.calls
