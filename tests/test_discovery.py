"""Path expansion."""

from __future__ import annotations

import os

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
