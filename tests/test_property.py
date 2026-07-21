"""Property-based (Hypothesis) tests for the pure-logic functions.

These fuzz the deterministic core (ports, templating, .env rewriting, port parsing)
to surface edge cases the example-based tests miss.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

import project_sandbox as ps


@given(
    st.dictionaries(st.text(min_size=1, max_size=12), st.integers(min_value=0, max_value=60000)),
    st.integers(min_value=0, max_value=10000),
)
def test_compute_ports_adds_offset_uniformly(defaults, offset):
    assert ps.compute_ports(defaults, offset) == {k: v + offset for k, v in defaults.items()}


@given(st.lists(st.integers(min_value=1, max_value=40)))
def test_allocate_offset_is_a_fresh_positive_multiple(offset_units):
    reg = {"instances": {f"i{n}": {"offset": u * ps.BLOCK_SIZE} for n, u in enumerate(offset_units)}}
    got = ps.allocate_offset(reg, "brand-new")
    assert got > 0 and got % ps.BLOCK_SIZE == 0
    assert got not in {i["offset"] for i in reg["instances"].values()}


@given(st.text(max_size=100))
def test_parse_port_entry_never_raises(s):
    host, container = ps._parse_port_entry(s)
    assert host is None or isinstance(host, int)
    assert isinstance(container, str)


@given(st.integers(min_value=1, max_value=65535), st.integers(min_value=1, max_value=65535))
def test_parse_port_entry_hostcontainer_roundtrip(host, container):
    h, c = ps._parse_port_entry(f"{host}:{container}")
    assert h == host and c == str(container)


@given(st.integers(min_value=1, max_value=65535))
def test_render_template_substitutes_known_key(port):
    assert ps.render_template("http://localhost:${P}", {"P": port}) == f"http://localhost:{port}"


@given(st.text(max_size=100).filter(lambda s: "${" not in s))
def test_render_template_returns_plain_text_unchanged(s):
    assert ps.render_template(s, {}) == s


def test_render_template_unknown_key_raises():
    with pytest.raises(ps.TemplateError):
        ps.render_template("${NOPE}", {})


@given(
    st.dictionaries(
        st.from_regex(r"[A-Z][A-Z0-9_]{0,10}", fullmatch=True),
        st.text(max_size=30).filter(lambda s: "\n" not in s and "=" not in s),
        max_size=6,
    )
)
def test_rewrite_env_lines_appends_each_key_once_and_ends_with_newline(rewrites):
    out = ps.rewrite_env_lines(["EXISTING=1\n"], rewrites)
    text = "".join(out)
    assert "EXISTING=1\n" in text  # unrelated lines preserved
    for k, v in rewrites.items():
        assert f"{k}={v}\n" in text
    assert out[-1].endswith("\n")
