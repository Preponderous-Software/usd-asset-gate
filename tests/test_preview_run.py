"""The preview pipeline's git layer and bookkeeping: changes_since, the base worktree,
file-name slugs, settings round trips, per-file error isolation and previews.json.

No Strata needed; tests/test_preview.py covers the pipeline's images and numbers.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

np = pytest.importorskip("numpy")

from conftest import PASS_DIR  # noqa: E402
from usd_asset_gate.discovery import DiscoveryError  # noqa: E402
from usd_asset_gate.preview import run as preview_run  # noqa: E402

# --- git ------------------------------------------------------------------------------


def _git(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def branched(tmp_path):
    """A base commit, a ``side`` branch that moves on from it, and HEAD with the change.

    HEAD modifies a.usda, deletes b.usda, renames c.usda to d.usda, adds sub/e.usdz and
    touches two non-USD files; ``side`` adds f.usda after the branches split.
    """
    for name in ("a.usda", "b.usda", "c.usda"):
        (tmp_path / name).write_text(f"#usda 1.0\n# {name} at base\n")
    (tmp_path / "notes.txt").write_text("base\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "branch", "side")

    (tmp_path / "a.usda").write_text("#usda 1.0\n# a.usda changed\n")
    _git(tmp_path, "rm", "-q", "b.usda")
    _git(tmp_path, "mv", "c.usda", "d.usda")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "e.usdz").write_bytes(b"PK\x03\x04")
    (tmp_path / "notes.txt").write_text("changed\n")
    (tmp_path / "README.md").write_text("new\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "change")

    _git(tmp_path, "checkout", "-q", "side")
    (tmp_path / "f.usda").write_text("#usda 1.0\n")
    _git(tmp_path, "add", "f.usda")
    _git(tmp_path, "commit", "-qm", "side moves on")
    _git(tmp_path, "checkout", "-q", "-")
    return tmp_path


def test_changes_since_lists_usd_files_against_the_merge_base(branched):
    changes = preview_run.changes_since("side", str(branched))
    assert [(c.path, c.kind) for c in changes] == [
        ("a.usda", "modified"),
        ("b.usda", "deleted"),
        ("c.usda", "deleted"),  # a rename is a deletion plus an addition
        ("d.usda", "added"),
        ("sub/e.usdz", "added"),
    ]


def test_changes_since_is_empty_without_usd_changes(branched):
    assert preview_run.changes_since("HEAD", str(branched)) == []


def test_changes_since_bad_ref_is_a_discovery_error(branched):
    with pytest.raises(DiscoveryError, match="no-such-ref"):
        preview_run.changes_since("no-such-ref", str(branched))


def _worktrees(repo) -> list[str]:
    return _git(repo, "worktree", "list").strip().splitlines()


def test_base_worktree_checks_out_the_merge_base_and_is_removed(branched):
    with preview_run.BaseWorktree("side", str(branched)) as path:
        base = pathlib.Path(path)
        assert len(_worktrees(branched)) == 2
        assert "at base" in (base / "a.usda").read_text()  # not HEAD's version
        assert (base / "c.usda").is_file()  # the rename's old side is there
        assert not (base / "f.usda").exists()  # nor the side branch's later commit
    assert not base.exists()
    assert len(_worktrees(branched)) == 1


def test_base_worktree_bad_ref_raises_and_leaves_nothing_behind(branched):
    tree = preview_run.BaseWorktree("no-such-ref", str(branched))
    with pytest.raises(DiscoveryError):
        with tree:
            pass
    assert tree.path is None
    assert len(_worktrees(branched)) == 1


# --- bookkeeping ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,slug",
    [
        ("assets/yard.usda", "assets_yard.usda"),
        ("a b/c:d.usdz", "a_b_c_d.usdz"),
        ("keep-this_one.usd", "keep-this_one.usd"),
    ],
)
def test_slug_keeps_only_file_name_safe_characters(path, slug):
    assert preview_run._slug(path) == slug


def test_settings_coerce_config_values_and_round_trip():
    s = preview_run.Settings.from_config({"size": ["320", 180.0], "max_triangles": "100", "threshold": 3})
    assert s.size == (320, 180) and s.max_triangles == 100
    assert s.threshold == 3.0 and isinstance(s.threshold, float)
    assert preview_run.Settings.from_config(s.to_dict()) == s
    assert preview_run.Settings.from_config(None) == preview_run.Settings()


class RecordingRenderer:
    """Fails every render; remembers instances so a test can see close() was called."""

    version, gpu = "fake", "none"
    made: list = []

    def __init__(self, width, height):
        self.closed = False
        RecordingRenderer.made.append(self)

    def render(self, *args):
        raise RuntimeError("draw call failed")

    def close(self):
        self.closed = True


def test_one_failing_file_does_not_hide_the_others(tmp_path):
    RecordingRenderer.made = []
    garbage = tmp_path / "garbage.usda"
    garbage.write_text("this is not a USD layer\n")
    files = [
        ("render-fails.usda", "added", None, str(PASS_DIR / "minimal.usda")),
        ("unopenable.usda", "modified", str(garbage), str(PASS_DIR / "minimal.usda")),
        ("lights.usda", "added", None, str(PASS_DIR / "yard_lights.usda")),
    ]
    doc = preview_run.run(files, str(tmp_path / "out"), preview_run.Settings(size=(16, 9)), RecordingRenderer)
    by = {f["path"]: f for f in doc["files"]}
    assert doc["status"] == "ok" and list(by) == [f[0] for f in files]
    assert by["render-fails.usda"] == {
        "path": "render-fails.usda",
        "change": "added",
        "status": "error",
        "message": "draw call failed",
        "views": [],
    }
    assert by["unopenable.usda"]["status"] == "error"
    assert by["unopenable.usda"]["message"].startswith("before: cannot flatten:")
    assert "after" not in by["unopenable.usda"]["triangles"]  # the after side is not tried
    assert by["lights.usda"]["status"] == "no_geometry"
    (renderer,) = RecordingRenderer.made
    assert renderer.closed


def test_no_files_never_starts_the_renderer(tmp_path):
    def factory(*_):
        raise AssertionError("renderer started for an empty change")

    out = tmp_path / "out"
    doc = preview_run.run([], str(out), preview_run.Settings(), factory)
    assert out.is_dir()
    assert doc["status"] == "ok" and doc["files"] == [] and "renderer" not in doc
    assert doc["not_drawn"] == preview_run.NOT_DRAWN
    assert doc["settings"] == preview_run.Settings().to_dict()


def test_write_json_is_indented_and_ends_with_a_newline(tmp_path):
    doc = {"version": 1, "files": [{"path": "a.usda"}]}
    path = tmp_path / "previews.json"
    preview_run.write_json(doc, str(path))
    text = path.read_text(encoding="utf-8")
    assert text == json.dumps(doc, indent=2) + "\n"
