import os
import textwrap
import time
import urllib.request
import subprocess
import pytest
import project_sandbox as ps

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "mini-stack")


def _http_ok(port, timeout=60):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://localhost:{port}", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception as e:
            last = e
            time.sleep(1)
    print("last error:", last)
    return False


@pytest.mark.slow
def test_parallel_instances(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    stacks = tmp_path / "cfg" / "project-sandbox" / "stacks"
    stacks.mkdir(parents=True)
    (stacks / "mini.yml").write_text(textwrap.dedent(f"""
        stack: mini
        network: mininet
        projects:
          - path: {FIXTURE}
            creates_network: true
        ports:
          WEB: 18080
    """))
    try:
        assert ps.main(["up", "mini", "--instance", "t1", "--path", FIXTURE]) == 0
        assert ps.main(["up", "mini", "--instance", "t2", "--path", FIXTURE]) == 0
        # deterministic offsets: t1 -> +100 (18180), t2 -> +200 (18280)
        assert _http_ok(18180), "t1 not responding on 18180"
        assert _http_ok(18280), "t2 not responding on 18280"
        reg = ps.load_registry()
        assert {"t1", "t2"} <= set(reg["instances"])
    finally:
        ps.main(["rm", "t1"])
        ps.main(["rm", "t2"])
    leftover = subprocess.run(
        ["docker", "ps", "-a", "--filter", "label=project-sandbox.stack=mini",
         "--format", "{{.Names}}"],
        capture_output=True, text=True).stdout.strip()
    assert leftover == "", f"leftover containers: {leftover}"
