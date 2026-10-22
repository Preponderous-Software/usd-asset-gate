"""--changed-since discovery in a throwaway git repository."""

from __future__ import annotations

import json
import subprocess

import pytest

from conftest import DEFAULT_CONFIG, PASS_DIR
from usd_asset_gate import discovery

VALID = (PASS_DIR / "minimal.usda").read_text()


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "symbolic-ref", "HEAD", "refs/heads/main")  # git < 2.28 has no init -b
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "keep.usda").write_text(VALID)
    (tmp_path / "assets" / "doomed.usda").write_text(VALID)
    (tmp_path / "assets" / "edit.usda").write_text(VALID)
    (tmp_path / "tool.py").write_text("print(1)\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "checkout", "-q", "-b", "feature")
    # The "pull request": edit one .usda, add one, edit a .py, delete a .usda.
    (tmp_path / "assets" / "edit.usda").write_text(VALID + "\n")
    (tmp_path / "assets" / "new.usda").write_text(VALID)
    (tmp_path / "tool.py").write_text("print(2)\n")
    (tmp_path / "assets" / "doomed.usda").unlink()
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "change")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_only_changed_usd_files_are_listed(repo):
    assert discovery.changed_files("main") == ["assets/edit.usda", "assets/new.usda"]


def test_pr_changing_one_usda_and_one_py_checks_only_the_usda(repo, cli):
    (repo / "assets" / "new.usda").unlink()
    git(repo, "commit", "-q", "-am", "drop new")
    code, out, _ = cli("check", "--config", str(DEFAULT_CONFIG), "--format", "json", "--changed-since", "main")
    assert code == 0
    assert [f["path"] for f in json.loads(out)["files"]] == ["assets/edit.usda"]


def test_deleted_file_is_not_checked(repo):
    assert "assets/doomed.usda" not in discovery.changed_files("main")


def test_paths_narrow_the_changed_set(repo, cli):
    (repo / "other").mkdir()
    (repo / "other" / "x.usda").write_text(VALID)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "other")
    code, out, _ = cli(
        "check", "--config", str(DEFAULT_CONFIG), "--format", "json", "--changed-since", "main", "other"
    )
    assert [f["path"] for f in json.loads(out)["files"]] == ["other/x.usda"]


def test_include_exclude_apply_to_changed_files(repo, cli):
    (repo / ".usd-asset-gate.yml").write_text("exclude: ['assets/new.usda']\n")
    code, out, _ = cli("check", "--format", "json", "--changed-since", "main")
    assert [f["path"] for f in json.loads(out)["files"]] == ["assets/edit.usda"]


def test_runs_from_a_subdirectory(repo, monkeypatch):
    monkeypatch.chdir(repo / "assets")
    assert discovery.changed_files("main") == ["edit.usda", "new.usda"]


def test_missing_ref_exits_2_with_fetch_hint(repo, cli):
    code, out, err = cli("check", "--changed-since", "origin/does-not-exist")
    assert code == 2
    assert "not present" in err and "git fetch" in err
    assert out == ""


def test_no_changes_checks_nothing_and_passes(repo, cli):
    code, out, _ = cli("check", "--config", str(DEFAULT_CONFIG), "--changed-since", "HEAD")
    assert code == 0
    assert out.startswith("PASS: 0 files checked")


def test_outside_a_git_repository_is_a_usage_error(tmp_path, monkeypatch, cli):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    code, _, err = cli("check", "--changed-since", "main")
    assert code == 2


def test_file_name_with_carriage_return_is_kept(repo):
    name = "assets/a\rb.usda"
    (repo / name).write_text(VALID)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "cr")
    assert name in discovery.changed_files("main")


def test_narrowing_from_a_subdirectory(repo, monkeypatch, cli):
    (repo / "other").mkdir()
    (repo / "other" / "x.usda").write_text(VALID)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "other")
    monkeypatch.chdir(repo / "assets")
    changed = discovery.changed_files("main")
    assert sorted(c for c in changed if discovery.under_any(c, ["."])) == ["edit.usda", "new.usda"]
    assert sorted(c for c in changed if discovery.under_any(c, [".."])) == sorted(changed)
    outside = str(repo / "other" / "x.usda").replace("\\", "/")
    assert [c for c in changed if discovery.under_any(c, ["../other"])] == [outside]


def test_renamed_file_is_listed_under_its_new_name(repo):
    git(repo, "mv", "assets/keep.usda", "assets/moved.usda")
    git(repo, "commit", "-q", "-m", "rename")
    changed = discovery.changed_files("main")
    assert "assets/moved.usda" in changed
    assert "assets/keep.usda" not in changed


def test_uppercase_extension_is_listed(repo):
    (repo / "assets" / "LOUD.USDA").write_text(VALID)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "loud")
    assert "assets/LOUD.USDA" in discovery.changed_files("main")


def test_changed_file_missing_from_the_working_tree_is_skipped(repo):
    (repo / "assets" / "new.usda").unlink()  # deleted locally, not committed
    assert discovery.changed_files("main") == ["assets/edit.usda"]


def test_changes_made_on_ref_after_the_branch_point_are_not_listed(repo):
    git(repo, "checkout", "-q", "main")
    (repo / "assets" / "main_only.usda").write_text(VALID)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "main moves on")
    git(repo, "checkout", "-q", "feature")
    assert discovery.changed_files("main") == ["assets/edit.usda", "assets/new.usda"]
