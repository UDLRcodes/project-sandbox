import subprocess
import project_sandbox as ps


def test_db_rewrite_templates_ports_and_runs(monkeypatch):
    manifest = {"db_rewrite": [
        {"service": "api-db", "command": ["sh", "-c", "echo ${API_WEB} ${LOGIN_APP}"]}]}
    r = ps.Runner(dry_run=True)
    ps._run_db_rewrites(r, manifest, "webapp-x", {"API_WEB": 8180, "LOGIN_APP": 8183})
    joined = [" ".join(c) for c in r.calls]
    assert any(
        j == "docker compose -p webapp-x exec -T api-db sh -c echo 8180 8183"
        for j in joined
    )


def test_db_rewrite_absent_is_noop():
    r = ps.Runner(dry_run=True)
    ps._run_db_rewrites(r, {}, "p", {})
    assert r.calls == []


def test_db_rewrite_leaves_shell_vars_untouched():
    # ${...} is templated by the tool; $SA_PASSWORD (no braces) must pass through to the container
    manifest = {"db_rewrite": [
        {"service": "db", "command": ["sh", "-c", 'sqlcmd -P "$SA_PASSWORD" -Q "x ${API_WEB}"']}]}
    r = ps.Runner(dry_run=True)
    ps._run_db_rewrites(r, manifest, "p", {"API_WEB": 8180})
    last = r.calls[-1]
    assert last[-1] == 'sqlcmd -P "$SA_PASSWORD" -Q "x 8180"'


def test_db_rewrite_retries_until_db_ready():
    state = {"n": 0}
    slept = []
    class Flaky(ps.Runner):
        def __init__(self):
            super().__init__(dry_run=False)
        def run(self, cmd, check=True, **kw):
            self.calls.append(list(cmd)); state["n"] += 1
            return subprocess.CompletedProcess(cmd, 0 if state["n"] >= 3 else 1)
    r = Flaky()
    ps._run_db_rewrites(
        r, {"db_rewrite": [{"service": "db", "command": ["true"], "retries": 5, "delay": 0}]},
        "p", {}, sleep_fn=lambda s: slept.append(s))
    assert state["n"] == 3      # retried until success
    assert len(slept) == 2      # slept between attempts


def test_manifest_validates_db_rewrite_shape(tmp_path, monkeypatch):
    import textwrap, pytest
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    stacks = tmp_path / "cfg" / "project-sandbox" / "stacks"; stacks.mkdir(parents=True)
    (stacks / "s.yml").write_text(textwrap.dedent("""
        stack: s
        projects: [{path: /a}]
        ports: {}
        db_rewrite:
          - service: db
    """))
    with pytest.raises(ps.ManifestError, match="command"):
        ps.load_manifest("s")
