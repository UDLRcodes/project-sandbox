import os

import project_sandbox as ps


def test_write_override_creates_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    ov = {"services": {"web": {"ports": ps.Override(["18080:80"])}}}
    path = ps.write_override("demo", "api", ov)
    assert os.path.exists(path)
    text = open(path).read()
    assert "!override" in text
    assert "web" in text
