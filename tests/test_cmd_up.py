import os, textwrap, json
import project_sandbox as ps

def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    code = tmp_path / "Code"
    for name, port in [("api", 8080), ("web", 9000)]:
        d = code / name
        d.mkdir(parents=True)
        (d / "docker-compose.yml").write_text(textwrap.dedent(f"""
            services:
              {name}-svc:
                image: nginx
                container_name: {name}-svc
                ports: ["{port}:80"]
                networks: [app_network]
            networks:
              app_network:
                external: true
        """))
    stacks = tmp_path / "cfg" / "project-sandbox" / "stacks"
    stacks.mkdir(parents=True)
    (stacks / "webapp.yml").write_text(textwrap.dedent(f"""
        stack: webapp
        network: app_network
        worktree_parent: {code}/.worktrees
        projects:
          - path: {code}/api
            creates_network: true
          - path: {code}/web
        ports:
          API_WEB: 8080
          WEB: 9000
        db_baseline:
          - service: api-svc
            volume: dbdata
    """))
    return code

def test_up_dry_run_full_flow(tmp_path, monkeypatch, capsys):
    code = _setup(tmp_path, monkeypatch)
    rc = ps.main(["--dry-run", "up", "webapp", "--instance", "demo", "--branch", "feat/x"])
    assert rc == 0
    reg = ps.load_registry()
    assert reg["instances"]["demo"]["project_name"] == "webapp-demo"
    assert reg["instances"]["demo"]["offset"] == 100
    # override files were written for both projects
    idir = ps.instance_dir("demo")
    assert os.path.exists(os.path.join(idir, "api.override.yml"))
    assert os.path.exists(os.path.join(idir, "web.override.yml"))

def test_up_dry_run_commands(tmp_path, monkeypatch):
    code = _setup(tmp_path, monkeypatch)
    # capture the runner by patching Runner to record globally
    recorded = []
    real_run = ps.Runner.run
    def spy(self, cmd, check=True, **kw):
        recorded.append(list(cmd)); return real_run(self, cmd, check=check, **kw)
    monkeypatch.setattr(ps.Runner, "run", spy)
    ps.main(["--dry-run", "up", "webapp", "--instance", "demo"])
    joined = [" ".join(c) for c in recorded]
    # network created, volume cloned, and compose up per project with the instance project name
    assert any("docker network create app_network_demo" in j for j in joined)
    assert any("docker volume create webapp-demo-dbdata" in j for j in joined)
    ups = [j for j in joined if "docker compose -p webapp-demo" in j and j.endswith("up -d")]
    assert len(ups) == 2  # both projects brought up


def test_up_sets_compose_ignore_orphans(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    envs = []
    real_run = ps.Runner.run
    def spy(self, cmd, check=True, **kw):
        if cmd[:3] == ["docker", "compose", "-p"] and "up" in cmd:
            envs.append(kw.get("env"))
        return real_run(self, cmd, check=check, **kw)
    monkeypatch.setattr(ps.Runner, "run", spy)
    ps.main(["--dry-run", "up", "webapp", "--instance", "z1"])
    assert envs and all(e and e.get("COMPOSE_IGNORE_ORPHANS") == "1" for e in envs)


def test_up_compose_failure_is_clean_and_recoverable(tmp_path, monkeypatch):
    import subprocess
    import pytest
    _setup(tmp_path, monkeypatch)

    class UpFails(ps.Runner):
        def run(self, cmd, check=True, **kw):
            self.calls.append(list(cmd))
            if cmd[:2] == ["docker", "compose"] and "up" in cmd:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 0)

    args = type("A", (), {"stack": "webapp", "instance": "zz", "branch": None, "path": None})
    with pytest.raises(ps.SandboxError, match="bring-up failed"):
        ps.cmd_up(args, UpFails(dry_run=True))
    # recorded before bring-up -> rm-able even though 'up' failed
    assert "zz" in ps.load_registry()["instances"]


def test_up_records_namespaced_volumes_for_cleanup(tmp_path, monkeypatch):
    import textwrap
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    app = tmp_path / "Code" / "app"; app.mkdir(parents=True)
    (app / "docker-compose.yml").write_text(textwrap.dedent("""
        services:
          app:
            image: nginx
            volumes: [data:/data]
        volumes:
          data: {}
    """))
    stacks = tmp_path / "cfg" / "project-sandbox" / "stacks"; stacks.mkdir(parents=True)
    (stacks / "s.yml").write_text(textwrap.dedent(f"""
        stack: s
        projects:
          - path: {app}
        ports: {{}}
    """))
    ps.main(["--dry-run", "up", "s", "--instance", "i1", "--path", str(app)])
    vols = ps.load_registry()["instances"]["i1"]["volumes"]
    assert "s-i1-app-data" in vols   # namespaced volume tracked so rm removes it
