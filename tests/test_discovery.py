"""Path expansion, glob filtering and git plumbing."""

from __future__ import annotations

import os
import subprocess

import pytest

from usd_asset_gate import discovery


def test_expand_walks_directories_and_keeps_usd_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.usda").write_text("#usda 1.0\n")
    (tmp_path / "a" / "y.USDZ").write_bytes(b"")
    (tmp_path / "a" / "z.py").write_text("")
    (tmp_path / "a" / ".hidden").mkdir()
    (tmp_path / "a" / ".hidden" / "h.usda").write_text("#usda 1.0\n")
    assert discovery.expand_paths(["a"]) == [("a/x.usda", False), ("a/y.USDZ", False)]


def test_explicit_file_is_marked_explicit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.usda").write_text("#usda 1.0\n")
    assert discovery.expand_paths(["x.usda", "."]) == [("x.usda", True)]


def test_unreadable_directory_is_an_error_not_a_silent_pass(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()

    def walk(top, onerror=None, **kwargs):
        if onerror is not None:
            onerror(PermissionError(13, "Permission denied", str(top)))
        return iter(())

    monkeypatch.setattr(discovery.os, "walk", walk)
    with pytest.raises(discovery.DiscoveryError, match="cannot read directory"):
        discovery.expand_paths([str(tmp_path / "assets")])


def test_display_path_falls_back_when_no_relative_path_exists(monkeypatch):
    def relpath(path, start=None):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(discovery.os.path, "relpath", relpath)
    result = discovery.display_path("/elsewhere/model.usda")
    assert result == os.path.abspath("/elsewhere/model.usda").replace(os.sep, "/")


def test_display_path_outside_cwd_is_absolute(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    other = tmp_path / "other.usda"
    assert discovery.display_path(str(other)) == str(other).replace(os.sep, "/")


def test_dotdot_prefixed_name_inside_cwd_stays_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert discovery.display_path("..crate.usda") == "..crate.usda"


# --- is_usd_file / expand_paths -----------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("a.usd", True),
        ("a.USDA", True),
        ("dir/a.UsDc", True),
        ("a.usdz", True),
        ("a.usdx", False),
        ("a.usda.bak", False),
        ("usda", False),
        ("a.py", False),
    ],
)
def test_is_usd_file_compares_extension_case_insensitively(name, expected):
    assert discovery.is_usd_file(name) is expected


def test_missing_path_is_an_error_naming_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(discovery.DiscoveryError, match="no such file or directory: nope.usda"):
        discovery.expand_paths(["nope.usda"])


def test_explicit_file_wins_when_named_after_its_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.usda").write_text("#usda 1.0\n")
    assert discovery.expand_paths([".", "x.usda"]) == [("x.usda", True)]


def test_explicit_file_is_kept_whatever_its_extension(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("")
    assert discovery.expand_paths(["notes.txt"]) == [("notes.txt", True)]


def test_hidden_directory_named_directly_is_walked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "h.usda").write_text("#usda 1.0\n")
    assert discovery.expand_paths([".hidden"]) == [(".hidden/h.usda", False)]


def test_overlapping_paths_are_deduplicated_and_sorted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "z.usda").write_text("#usda 1.0\n")
    (tmp_path / "a" / "b" / "c.usda").write_text("#usda 1.0\n")
    assert discovery.expand_paths(["a/b", "a", "a"]) == [("a/b/c.usda", False), ("a/z.usda", False)]


# --- glob matching ------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern, path, expected",
    [
        ("assets/**", "assets", False),
        ("a**b.usda", "a/x/b.usda", True),
        ("a.usda", "axusda", False),
        ("a?usda", "a/usda", False),
        ("*.usda", "a.usda.bak", False),
    ],
)
def test_glob_edge_cases(pattern, path, expected):
    assert bool(discovery.glob_to_regex(pattern).match(path)) is expected


def test_matches_glob_with_no_patterns_is_false():
    assert discovery.matches_glob("a.usda", []) is False


def test_empty_include_keeps_nothing_unlike_none():
    paths = ["a.usda", "b/c.usda"]
    assert discovery.filter_paths(paths, [], []) == []
    assert discovery.filter_paths(paths, None, []) == paths


def test_filter_paths_reuses_one_shot_pattern_iterables():
    paths = ["assets/a.usda", "assets/b_wip.usda", "assets/c.usda", "other/d.usda"]
    include = (p for p in ["assets/**"])
    exclude = (p for p in ["**/*_wip.usda"])
    assert discovery.filter_paths(paths, include, exclude) == ["assets/a.usda", "assets/c.usda"]


# --- under_any ----------------------------------------------------------------


def test_under_any_matches_the_root_itself_and_its_contents(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert discovery.under_any("assets", ["assets"])
    assert discovery.under_any("assets/a/b.usda", ["assets/"])
    assert discovery.under_any(str(tmp_path / "assets" / "x.usda"), ["./assets"])


def test_under_any_does_not_match_a_sibling_sharing_a_prefix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not discovery.under_any("assets2/x.usda", ["assets"])
    assert not discovery.under_any("x.usda", [])


# --- git plumbing -------------------------------------------------------------


def _no_git(*args, **kwargs):
    raise FileNotFoundError("git")


def test_git_missing_is_a_discovery_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _no_git)
    with pytest.raises(discovery.DiscoveryError, match="git is not installed or not on PATH"):
        discovery._git(["status"])


def test_changed_files_without_git_is_a_discovery_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _no_git)
    with pytest.raises(discovery.DiscoveryError, match="git is not installed or not on PATH"):
        discovery.changed_files("main")


def test_git_failure_reports_stderr(monkeypatch):
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 128, b"", b"fatal: bad revision\n")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(discovery.DiscoveryError, match=r"^git diff x\.\.\.HEAD failed: fatal: bad revision$"):
        discovery._git(["diff", "x...HEAD"])


def test_git_failure_without_stderr_reports_exit_code(monkeypatch):
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 3, b"", b"  \n")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(discovery.DiscoveryError, match=r"^git status failed: exit code 3$"):
        discovery._git(["status"])


def test_git_returns_raw_stdout_bytes(monkeypatch):
    def run(args, **kwargs):
        assert args == ["git", "ls-files", "-z"]
        assert kwargs["cwd"] == "/somewhere"
        return subprocess.CompletedProcess(args, 0, b"a\r\nb\0", b"")

    monkeypatch.setattr(subprocess, "run", run)
    assert discovery._git(["ls-files", "-z"], cwd="/somewhere") == b"a\r\nb\0"
