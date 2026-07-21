import project_sandbox as ps


def test_version(capsys):
    rc = ps.main(["--version"])
    assert rc == 0
    assert ps.__version__ in capsys.readouterr().out


def test_up_requires_instance(capsys):
    rc = ps.main(["up", "webapp"])
    assert rc != 0


def test_no_command_prints_help(capsys):
    rc = ps.main([])
    assert rc == 1


def test_dispatch_by_name(monkeypatch):
    called = {}

    def _fake_ls(a, r):
        called["ls"] = True
        return 0

    monkeypatch.setattr(ps, "cmd_ls", _fake_ls)
    rc = ps.main(["ls"])
    assert rc == 0 and called.get("ls")


def test_help_lists_commands_and_examples(capsys):
    rc = ps.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    # every command is listed
    for cmd in ("up", "down", "rm", "ls", "status", "init", "baseline", "logs", "exec"):
        assert cmd in out
    # a one-line description and worked examples are present
    assert "isolated, parallel copies" in out
    assert "Examples:" in out
    assert ",project-sandbox up webapp --instance" in out


def test_subcommand_help_has_description(capsys):
    rc = ps.main(["up", "--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "instance" in out and "worktree" in out.lower()
