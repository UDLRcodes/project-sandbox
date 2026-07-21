# tests/test_ports.py
import project_sandbox as ps


def test_first_offset_is_block():
    reg = {"instances": {}}
    assert ps.allocate_offset(reg, "a") == 100


def test_offset_is_idempotent():
    reg = {"instances": {"a": {"offset": 300}}}
    assert ps.allocate_offset(reg, "a") == 300


def test_offset_skips_used():
    reg = {"instances": {"a": {"offset": 100}, "b": {"offset": 200}}}
    assert ps.allocate_offset(reg, "c") == 300


def test_offset_fills_lowest_gap():
    reg = {"instances": {"a": {"offset": 100}, "c": {"offset": 300}}}
    assert ps.allocate_offset(reg, "b") == 200


def test_compute_ports():
    assert ps.compute_ports({"API_WEB": 8080, "MSSQL": 1433}, 100) == {
        "API_WEB": 8180,
        "MSSQL": 1533,
    }


def test_is_port_free_on_bound_socket():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    taken = s.getsockname()[1]
    try:
        assert ps.is_port_free(taken) is False
    finally:
        s.close()


def test_verify_ports_free_reports_taken():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    taken = s.getsockname()[1]
    try:
        assert ps.verify_ports_free({"X": taken, "Y": 0}) == [
            taken
        ] or taken in ps.verify_ports_free({"X": taken})
    finally:
        s.close()


def test_should_check_ports_new_instance():
    # brand-new instance in a real run -> must verify host ports are free
    assert ps._should_check_ports({"instances": {}}, "a", dry_run=False) is True


def test_should_check_ports_existing_instance():
    # re-up of a known instance is an idempotent reconcile -> skip the check
    reg = {"instances": {"a": {"offset": 100}}}
    assert ps._should_check_ports(reg, "a", dry_run=False) is False


def test_should_check_ports_dry_run():
    assert ps._should_check_ports({"instances": {}}, "a", dry_run=True) is False
