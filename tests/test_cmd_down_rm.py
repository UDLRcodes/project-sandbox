import pytest
import project_sandbox as ps

def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    ps.save_registry({"instances": {"demo": {
        "stack": "webapp", "offset": 100, "ports": {"API_WEB": 8180},
        "project_name": "webapp-demo", "network": "app_network_demo",
        "volumes": ["webapp-demo-dbdata"],
        "projects": ["/Code/api", "/Code/web"],
        "worktrees": ["/wt/api", "/wt/web"], "path_override": None}}})

def test_down(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    r = ps.Runner(dry_run=True)
    ps.cmd_down(type("A", (), {"instance": "demo"}), r)
    assert ["docker", "compose", "-p", "webapp-demo", "down"] in r.calls

def test_rm_full_teardown(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    r = ps.Runner(dry_run=True)
    ps.cmd_rm(type("A", (), {"instance": "demo", "keep_worktree": False}), r)
    joined = [" ".join(c) for c in r.calls]
    assert any("docker compose -p webapp-demo down -v" in j for j in joined)
    assert any("docker volume rm webapp-demo-dbdata" in j for j in joined)
    assert any("docker network rm app_network_demo" in j for j in joined)
    assert any("git -C /Code/api worktree remove /wt/api --force" in j for j in joined)
    assert "demo" not in ps.load_registry()["instances"]

def test_rm_keep_worktree(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    r = ps.Runner(dry_run=True)
    ps.cmd_rm(type("A", (), {"instance": "demo", "keep_worktree": True}), r)
    assert not any("worktree remove" in " ".join(c) for c in r.calls)

def test_down_unknown_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    with pytest.raises(ps.SandboxError):
        ps.cmd_down(type("A", (), {"instance": "nope"}), ps.Runner(dry_run=True))

def test_worktree_remove_is_tolerant():
    import subprocess
    seen = {}
    class Spy(ps.Runner):
        def run(self, cmd, check=True, **kw):
            seen["check"] = check
            return subprocess.CompletedProcess(cmd, 0)
    ps.worktree_remove(Spy(dry_run=False), "/repo", "/wt/x")
    assert seen["check"] is False   # never abort teardown on a missing worktree

def test_rm_completes_when_resources_missing(tmp_path, monkeypatch, capsys):
    # Simulates rm on a partial/dry-run instance: every docker/git call fails.
    _seed(tmp_path, monkeypatch)
    import subprocess
    class Flaky(ps.Runner):
        def run(self, cmd, check=True, **kw):
            self.calls.append(list(cmd))
            if check:
                raise subprocess.CalledProcessError(128, cmd)
            return subprocess.CompletedProcess(cmd, 1)
    rc = ps.cmd_rm(type("A", (), {"instance": "demo", "keep_worktree": False}), Flaky(dry_run=False))
    assert rc == 0
    assert "demo" not in ps.load_registry()["instances"]  # entry always purged


def test_rm_skips_absent_resources_quietly(tmp_path, monkeypatch):
    # Real mode, but every resource is already gone (a phantom/half-created instance).
    # rm must NOT issue removal commands for absent resources (no scary not-found noise),
    # yet still purge the registry entry.
    import subprocess
    _seed(tmp_path, monkeypatch)   # worktrees /wt/api,/wt/web do not exist on disk

    class AllAbsent(ps.Runner):
        def __init__(self):
            super().__init__(dry_run=False)
        def run(self, cmd, check=True, **kw):
            self.calls.append(list(cmd))
            rc = 1 if "inspect" in cmd else 0          # inspects => resource absent
            if check and rc:
                raise subprocess.CalledProcessError(rc, cmd)
            return subprocess.CompletedProcess(cmd, rc)
        def capture(self, cmd, check=True):
            self.calls.append(list(cmd))
            return ""                                   # no containers for the project

    r = AllAbsent()
    ps.cmd_rm(type("A", (), {"instance": "demo", "keep_worktree": False}), r)
    joined = [" ".join(c) for c in r.calls]
    assert not any("volume rm" in j for j in joined)          # absent -> skipped
    assert not any("network rm" in j for j in joined)         # absent -> skipped
    assert not any("down" in j for j in joined)               # no containers -> skipped
    assert not any("worktree remove" in j for j in joined)    # paths absent -> skipped
    assert "demo" not in ps.load_registry()["instances"]   # still purged
