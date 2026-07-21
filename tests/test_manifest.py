# tests/test_manifest.py
import textwrap

import pytest

import project_sandbox as ps


def _write(tmp_path, monkeypatch, body):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    p = tmp_path / "cfg" / "project-sandbox" / "stacks"
    p.mkdir(parents=True)
    (p / "webapp.yml").write_text(textwrap.dedent(body))


def test_load_expands_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/home/tester")
    _write(
        tmp_path,
        monkeypatch,
        """
        stack: webapp
        network: app_network
        worktree_parent: ~/Code/.worktrees
        projects:
          - path: ~/Code/api
            creates_network: true
        ports:
          API_WEB: 8080
    """,
    )
    m = ps.load_manifest("webapp")
    assert m["projects"][0]["path"] == "/home/tester/Code/api"
    assert m["worktree_parent"] == "/home/tester/Code/.worktrees"


def test_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    with pytest.raises(ps.ManifestError, match="not found"):
        ps.load_manifest("nope")


def test_missing_required_key_raises(tmp_path, monkeypatch):
    _write(
        tmp_path,
        monkeypatch,
        """
        stack: webapp
        ports: {API_WEB: 8080}
    """,
    )
    with pytest.raises(ps.ManifestError, match="projects"):
        ps.load_manifest("webapp")


def test_two_network_creators_raises(tmp_path, monkeypatch):
    _write(
        tmp_path,
        monkeypatch,
        """
        stack: webapp
        network: app_network
        projects:
          - {path: /a, creates_network: true}
          - {path: /b, creates_network: true}
        ports: {API_WEB: 8080}
    """,
    )
    with pytest.raises(ps.ManifestError, match="exactly one"):
        ps.load_manifest("webapp")
