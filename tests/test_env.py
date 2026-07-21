# tests/test_env.py
import pytest
import project_sandbox as ps


def test_render_template():
    assert ps.render_template("http://localhost:${API_WEB}", {"API_WEB": 8180}) \
        == "http://localhost:8180"


def test_render_unknown_placeholder_raises():
    with pytest.raises(ps.TemplateError, match="NOPE"):
        ps.render_template("${NOPE}", {"API_WEB": 1})


def test_rewrite_replaces_existing_key():
    lines = ["APP_URL=http://old\n", "OTHER=keepme\n"]
    out = ps.rewrite_env_lines(lines, {"APP_URL": "http://localhost:8180"})
    assert out == ["APP_URL=http://localhost:8180\n", "OTHER=keepme\n"]


def test_rewrite_appends_missing_key():
    out = ps.rewrite_env_lines(["OTHER=x\n"], {"APP_URL": "http://localhost:8180"})
    assert "APP_URL=http://localhost:8180\n" in out
    assert out[0] == "OTHER=x\n"


def test_rewrite_appends_on_own_line_when_file_has_no_trailing_newline():
    # a .env whose last line lacks a trailing newline must not get the appended key glued on
    out = ps.rewrite_env_lines(["FOO=bar"], {"NEW": "x"})
    assert out == ["FOO=bar\n", "NEW=x\n"]
