import project_sandbox as ps


def test_missing_docker(monkeypatch):
    monkeypatch.setattr(ps.shutil, "which", lambda x: None if x == "docker" else f"/usr/bin/{x}")
    missing = ps.check_dependencies()
    assert any(name == "docker" for name, _ in missing)


def test_all_present(monkeypatch):
    monkeypatch.setattr(ps.shutil, "which", lambda x: f"/usr/bin/{x}")
    assert ps.check_dependencies() == []
