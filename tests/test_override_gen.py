# tests/test_override_gen.py
import project_sandbox as ps


def _compose():
    return {
        "services": {
            "api-web": {
                "image": "nginx",
                "container_name": "api-web",
                "ports": ["8080:80"],
                "networks": ["app_network"],
            },
            "api-php": {"image": "php", "networks": ["app_network"]},
        },
        "networks": {"app_network": {"external": True}},
    }


def test_override_sets_ports_and_container_name_and_network():
    ov = ps.build_override(
        compose=_compose(),
        project={},
        ports={"API_WEB": 18080},
        instance="demo",
        network_name="app_network_demo",
        port_key_by_service={"api-web": {"80": "API_WEB"}},
    )
    svc = ov["services"]["api-web"]
    assert list(svc["ports"]) == ["18080:80"]
    assert isinstance(svc["ports"], ps.Override)
    assert svc["container_name"] == "api-web_demo"
    assert ov["networks"]["app_network"] == {"name": "app_network_demo", "external": True}
    # service without published ports gets no ports key
    assert "ports" not in ov["services"].get("api-php", {})


def test_volume_remap_and_toplevel_external():
    compose = {
        "services": {
            "api-db": {
                "image": "mssql",
                "volumes": ["sqlserverdata:/var/opt/mssql/data", "./backups:/backups"],
            }
        },
        "volumes": {"sqlserverdata": {}},
    }
    ov = ps.build_override(
        compose=compose,
        project={},
        ports={},
        instance="demo",
        network_name=None,
        port_key_by_service={},
        volume_renames={"sqlserverdata": "webapp-demo-sqlserverdata"},
    )
    svc = ov["services"]["api-db"]
    assert "webapp-demo-sqlserverdata:/var/opt/mssql/data" in svc["volumes"]
    assert "./backups:/backups" in svc["volumes"]
    assert ov["volumes"]["webapp-demo-sqlserverdata"] == {
        "name": "webapp-demo-sqlserverdata",
        "external": True,
    }


def test_build_args_and_per_instance_image():
    compose = {"services": {"login-app": {"image": "example/login:dev", "build": {"context": "."}}}}
    ov = ps.build_override(
        compose=compose,
        project={
            "rebuild_per_instance": True,
            "build_args": {"BACKEND_URL": "http://localhost:${API_WEB}"},
        },
        ports={"API_WEB": 18080},
        instance="demo",
        network_name=None,
        port_key_by_service={},
    )
    svc = ov["services"]["login-app"]
    assert svc["build"]["args"]["BACKEND_URL"] == "http://localhost:18080"
    assert svc["image"] == "example/login:dev-demo"


def test_build_override_namespaces_volumes():
    compose = {
        "services": {"api-php": {"image": "php", "volumes": ["vendor:/app/vendor", "./:/app"]}},
        "volumes": {"vendor": {}},
    }
    ov = ps.build_override(
        compose=compose,
        project={},
        ports={},
        instance="cir1",
        network_name=None,
        port_key_by_service={},
        volume_names={"vendor": "webapp-cir1-api-vendor"},
    )
    # top-level volume gets an explicit, project-unique name (service key unchanged)
    assert ov["volumes"]["vendor"] == {"name": "webapp-cir1-api-vendor"}


def test_namespaced_volume_names_disjoint_and_excludes_baseline():
    c1 = {"volumes": {"vendor": {}, "sqlserverdata": {}}}
    c2 = {"volumes": {"vendor": {}}}
    n1 = ps._namespaced_volume_names("webapp-x", "api", c1, {"sqlserverdata"})
    n2 = ps._namespaced_volume_names("webapp-x", "web", c2, set())
    assert n1["vendor"] == "webapp-x-api-vendor"
    assert n2["vendor"] == "webapp-x-web-vendor"
    assert n1["vendor"] != n2["vendor"]  # no collision across projects
    assert "sqlserverdata" not in n1  # db_baseline volume excluded
