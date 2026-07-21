# tests/test_registry.py
import project_sandbox as ps


def test_load_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert ps.load_registry() == {"instances": {}}


def test_save_then_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    reg = {"instances": {"demo": {"stack": "webapp", "offset": 100}}}
    ps.save_registry(reg)
    assert ps.load_registry() == reg
