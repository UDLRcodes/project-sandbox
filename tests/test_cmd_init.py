import os
import textwrap

import pytest

import project_sandbox as ps


def test_init_generates_valid_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    api = tmp_path / "api"
    api.mkdir()
    (api / "docker-compose.yml").write_text(
        textwrap.dedent("""
        services:
          api-web:
            image: nginx
            ports: ["8080:80"]
            networks: [app_network]
        networks:
          app_network: {external: true}
    """)
    )
    (api / ".env.example").write_text("APP_URL=http://localhost:8080\n")
    rc = ps.cmd_init(
        type("A", (), {"stack": "webapp", "project": [str(api)], "force": False}),
        ps.Runner(dry_run=False),
    )
    assert rc == 0
    text = open(ps.manifest_path("webapp")).read()
    assert "stack: webapp" in text
    assert "API_WEB_80: 8080" in text
    assert f"path: {api}" in text
    assert "network: app_network" in text
    assert "creates_network: true" in text
    assert "APP_URL" in text  # env suggestion present
    # the generated draft must itself be a loadable manifest
    m = ps.load_manifest("webapp")
    assert m["ports"]["API_WEB_80"] == 8080


def test_init_refuses_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    api = tmp_path / "api"
    api.mkdir()
    (api / "docker-compose.yml").write_text("services: {}\n")
    p = ps.manifest_path("webapp")
    os.makedirs(os.path.dirname(p))
    open(p, "w").write("existing")
    with pytest.raises(ps.SandboxError, match="already exists"):
        ps.cmd_init(
            type("A", (), {"stack": "webapp", "project": [str(api)], "force": False}),
            ps.Runner(dry_run=False),
        )
