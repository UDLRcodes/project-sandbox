# tests/test_paths.py
import project_sandbox as ps


def test_config_home_respects_xdg(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/cfg")
    assert ps.stacks_dir() == "/tmp/cfg/project-sandbox/stacks"


def test_config_home_default(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/tester")
    assert ps.stacks_dir() == "/home/tester/.config/project-sandbox/stacks"


def test_state_paths(monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/state")
    assert ps.registry_path() == "/tmp/state/project-sandbox/registry.json"
    assert ps.instance_dir("demo") == "/tmp/state/project-sandbox/instances/demo"


def test_manifest_path(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/cfg")
    assert ps.manifest_path("webapp") == "/tmp/cfg/project-sandbox/stacks/webapp.yml"
