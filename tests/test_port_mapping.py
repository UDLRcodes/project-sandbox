# tests/test_port_mapping.py
import project_sandbox as ps


def test_derive_maps_matching_host_ports():
    compose = {
        "services": {
            "api-web": {"ports": ["8080:80"]},
            "api-db": {"ports": ["1433:1433"]},
            "api-redis": {"ports": ["6379:6379"]},
        }
    }
    mapping, unmatched = ps.derive_port_key_map(compose, {"API_WEB": 8080, "MSSQL": 1433})
    assert mapping == {"api-web": {"80": "API_WEB"}, "api-db": {"1433": "MSSQL"}}
    assert unmatched == [6379]


def test_derive_handles_long_form():
    compose = {"services": {"web": {"ports": [{"published": 8080, "target": 80}]}}}
    mapping, _ = ps.derive_port_key_map(compose, {"API_WEB": 8080})
    assert mapping == {"web": {"80": "API_WEB"}}
