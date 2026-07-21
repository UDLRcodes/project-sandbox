# tests/test_runner.py
import project_sandbox as ps


def test_dry_run_records_and_skips(capsys):
    r = ps.Runner(dry_run=True)
    r.run(["docker", "compose", "up", "-d"])
    assert r.calls == [["docker", "compose", "up", "-d"]]
    assert "docker compose up -d" in capsys.readouterr().out


def test_real_run_executes():
    r = ps.Runner(dry_run=False)
    out = r.capture(["printf", "hello"])
    assert out == "hello"
