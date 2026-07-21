import pytest

import project_sandbox as ps


def test_worktree_dest():
    m = {"stack": "webapp", "worktree_parent": "/wt"}
    assert ps.worktree_dest(m, "demo", "/home/u/Code/api") == "/wt/webapp/demo/api"


def test_worktree_dest_unset_raises():
    with pytest.raises(ps.ManifestError, match="worktree_parent"):
        ps.worktree_dest({"stack": "webapp"}, "x", "/a/b")


def test_worktree_add_existing_branch():
    r = ps.Runner(dry_run=True)
    ps.worktree_add(r, "/repo", "/wt/x", branch="feature/y")
    assert r.calls == [["git", "-C", "/repo", "worktree", "add", "/wt/x", "feature/y"]]


def test_worktree_add_new_branch():
    r = ps.Runner(dry_run=True)
    ps.worktree_add(r, "/repo", "/wt/x", branch="feature/y", create_branch=True)
    assert r.calls == [["git", "-C", "/repo", "worktree", "add", "-b", "feature/y", "/wt/x"]]


def test_worktree_remove():
    r = ps.Runner(dry_run=True)
    ps.worktree_remove(r, "/repo", "/wt/x")
    assert r.calls == [["git", "-C", "/repo", "worktree", "remove", "/wt/x", "--force"]]


def test_remove_if_empty(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    full = tmp_path / "full"
    (full / "x").mkdir(parents=True)
    assert ps._remove_if_empty(str(empty)) is True
    assert not empty.exists()
    assert ps._remove_if_empty(str(full)) is False  # non-empty is left alone
    assert full.exists()
    assert ps._remove_if_empty(str(tmp_path / "missing")) is False  # tolerant
