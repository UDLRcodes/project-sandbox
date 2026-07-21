import textwrap
import subprocess
import pytest
import project_sandbox as ps

def _manifest(tmp_path, monkeypatch, with_db=True):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    stacks = tmp_path / "cfg" / "project-sandbox" / "stacks"
    stacks.mkdir(parents=True)
    db = "\ndb_baseline:\n  - {service: api-db, volume: sqlserverdata}\n" if with_db else ""
    (stacks / "webapp.yml").write_text(textwrap.dedent("""
        stack: webapp
        projects:
          - path: /Code/api
        ports: {API_WEB: 8080}
    """) + db)

class ExistingRunner(ps.Runner):
    def __init__(self):
        super().__init__(dry_run=False)
    def run(self, cmd, check=True, **kw):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

def test_baseline_creates_volume(tmp_path, monkeypatch):
    _manifest(tmp_path, monkeypatch)
    r = ps.Runner(dry_run=True)
    rc = ps.cmd_baseline(type("A", (), {"stack": "webapp", "force": False}), r)
    assert rc == 0
    assert any("docker volume create webapp-db-baseline" in " ".join(c) for c in r.calls)

def test_baseline_no_db(tmp_path, monkeypatch, capsys):
    _manifest(tmp_path, monkeypatch, with_db=False)
    rc = ps.cmd_baseline(type("A", (), {"stack": "webapp", "force": False}), ps.Runner(dry_run=True))
    assert rc == 0
    assert "no db_baseline" in capsys.readouterr().out

def test_baseline_exists_guard(tmp_path, monkeypatch):
    _manifest(tmp_path, monkeypatch)
    with pytest.raises(ps.SandboxError, match="already exists"):
        ps.cmd_baseline(type("A", (), {"stack": "webapp", "force": False}), ExistingRunner())
