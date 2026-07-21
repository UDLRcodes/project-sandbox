# tests/test_override_yaml.py
import yaml
import project_sandbox as ps


def test_override_tag_emitted():
    ps.register_yaml_tags()
    doc = {"ports": ps.Override(["18080:80"])}
    text = yaml.dump(doc, Dumper=ps.SandboxDumper, default_flow_style=False)
    assert "!override" in text
    assert "18080:80" in text
