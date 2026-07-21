import project_sandbox as ps

# ---- _seed_worktree_files ----


def test_seed_copies_env_and_extra_when_missing(tmp_path):
    src = tmp_path / "src"
    (src / "storage").mkdir(parents=True)
    (src / ".env").write_text("DB_PASSWORD=secret\n")
    (src / "storage" / "key.pem").write_text("KEY")
    dst = tmp_path / "wt"
    dst.mkdir()
    ps._seed_worktree_files(str(src), str(dst), ["storage/key.pem"])
    assert (dst / ".env").read_text() == "DB_PASSWORD=secret\n"
    assert (dst / "storage" / "key.pem").read_text() == "KEY"


def test_seed_does_not_clobber_existing(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / ".env").write_text("FROM_SRC\n")
    dst = tmp_path / "wt"
    dst.mkdir()
    (dst / ".env").write_text("EXISTING\n")
    ps._seed_worktree_files(str(src), str(dst), None)
    assert (dst / ".env").read_text() == "EXISTING\n"  # left alone


def test_seed_skips_self_copy(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / ".env").write_text("X\n")
    ps._seed_worktree_files(str(d), str(d), None)  # source == dest: no-op, no error
    assert (d / ".env").read_text() == "X\n"


# ---- _prewarm_volume ----


def test_prewarm_emits_create_and_populate(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    r = ps.Runner(dry_run=True)  # volume_exists -> False in dry-run, so it populates
    ps._prewarm_volume(
        r, "webapp-x-api-vendor", "vendor", "webapp-x", "/app/vendor", "example/api-php:dev"
    )
    joined = [" ".join(c) for c in r.calls]
    assert any("volume create" in j and "webapp-x-api-vendor" in j for j in joined)
    assert any("com.docker.compose.project=webapp-x" in j for j in joined)  # adopted by compose
    assert any("com.docker.compose.volume=vendor" in j for j in joined)
    assert any(
        "docker run --rm -v webapp-x-api-vendor:/app/vendor example/api-php:dev true" in j
        for j in joined
    )


def test_prewarm_skips_when_image_unknown(capsys):
    r = ps.Runner(dry_run=True)
    ps._prewarm_volume(r, "webapp-x-x-vendor", "vendor", "webapp-x", "/app/vendor", None)
    assert r.calls == []
    assert "prewarm skipped" in capsys.readouterr().err
