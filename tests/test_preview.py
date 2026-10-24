"""The advisory preview: ΔE policy, PNG output, cameras, the pipeline with a fake renderer,
the CLI (exit codes, git worktree, Strata unavailable), config and the comment section.

Nothing here needs Strata; tests/test_preview_render.py does.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import zlib

import pytest

np = pytest.importorskip("numpy")

from conftest import PASS_DIR  # noqa: E402
from usd_asset_gate.config import ConfigError, parse  # noqa: E402
from usd_asset_gate.model import Report  # noqa: E402
from usd_asset_gate.preview import compare as cmp  # noqa: E402
from usd_asset_gate.preview import run as preview_run  # noqa: E402
from usd_asset_gate.report import comment, previews  # noqa: E402

# --- ΔE -------------------------------------------------------------------------------


def _img(color, shape=(4, 5)):
    out = np.zeros((*shape, 3), dtype=np.uint8)
    out[:] = color
    return out


def test_identical_images_have_no_difference():
    a = _img((10, 200, 30))
    result, diff = cmp.compare(a, a.copy())
    assert result.pixels_over == 0 and result.max_delta_e == 0 and not result.visible_change
    assert (diff[..., 0] == diff[..., 1]).all()  # grey only, no red


def test_black_against_white_is_delta_e_100():
    result, _ = cmp.compare(_img((0, 0, 0)), _img((255, 255, 255)))
    assert result.max_delta_e == pytest.approx(100.0, abs=1e-3)
    assert result.fraction_over == 1.0 and result.visible_change


def test_known_lab_value():
    # sRGB (255, 0, 0) is L*a*b* (53.24, 80.09, 67.20) under D65.
    lab = cmp._lab(np.array([[[255, 0, 0]]], dtype=np.uint8))[0, 0]
    assert lab == pytest.approx([53.24, 80.09, 67.20], abs=0.02)


def test_threshold_and_max_fraction_follow_strata_policy():
    a = _img((128, 128, 128), (100, 100))
    b = a.copy()
    b[0, :50] = (255, 0, 0)  # 50 of 10,000 pixels = 0.5 %: still "no visible change"
    result, diff = cmp.compare(a, b)
    assert result.pixels_over == 50 and result.fraction_over == 0.005
    assert not result.visible_change
    assert (diff[0, :50, 0] > 128).all() and (diff[0, :50, 1:] == 0).all()
    b[0, 50] = (255, 0, 0)
    assert cmp.compare(a, b)[0].visible_change
    # a ΔE of about 1 is under the default threshold of 4
    c = a.copy()
    c[:] = (129, 128, 128)
    assert cmp.compare(a, c)[0].pixels_over == 0


def test_size_mismatch_is_an_error():
    with pytest.raises(ValueError):
        cmp.compare(_img(0, (2, 2)), _img(0, (2, 3)))


def test_strip_and_png_round_trip(tmp_path):
    a, b = _img((255, 0, 0), (3, 2)), _img((0, 0, 255), (3, 4))
    s = cmp.strip([a, b], gap=1)
    assert s.shape == (3, 7, 3)
    assert (s[:, 2] == 255).all()  # the gap is white
    path = tmp_path / "s.png"
    cmp.write_png(str(path), s)
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    assert (w, h) == (7, 3)
    start = data.index(b"IDAT") + 4
    (n,) = struct.unpack(">I", data[start - 8 : start - 4])
    raw = zlib.decompress(data[start : start + n])
    rows = np.frombuffer(raw, dtype=np.uint8).reshape(3, 1 + 7 * 3)
    assert (rows[:, 0] == 0).all()
    assert (rows[:, 1:].reshape(3, 7, 3) == s).all()


# --- cameras --------------------------------------------------------------------------


@pytest.mark.parametrize("view", sorted(preview_run.VIEW_DIRECTIONS))
def test_camera_keeps_the_bounding_sphere_in_front(view):
    centre, radius = np.array([1.0, 2.0, 3.0]), 2.0
    eye, target, near, far = preview_run.camera_for(view, centre, radius, 16 / 9)
    dist = np.linalg.norm(eye - centre)
    assert near < dist - radius and far > dist + radius
    assert dist * np.sin(np.radians(20)) >= radius  # fits the 40° vertical field of view
    assert (target == centre).all()


def test_frame_bounds_is_the_union():
    from usd_asset_gate.preview.flatten import flatten_file

    a = flatten_file(str(PASS_DIR / "minimal.usda"))
    b = flatten_file(str(PASS_DIR / "yard.usda"))
    centre, radius = preview_run.frame_bounds([a, None, b])
    assert np.allclose(centre, [1, 0, 0])
    assert preview_run.frame_bounds([None]) is None


# --- pipeline with a fake renderer ----------------------------------------------------


class FakeRenderer:
    """Draws a white square whose position follows the geometry's centroid."""

    version, gpu = "fake", "none"

    def __init__(self, width, height):
        self.w, self.h = width, height
        self.closed = False

    def render(self, batches, eye, target, fov, near, far):
        img = np.full((self.h, self.w, 3), 40, dtype=np.uint8)
        pos = np.concatenate([b[0] for b in batches])
        x = int(abs(float(pos[:, 0].mean())) * 10) % (self.w - 4)
        img[0:4, x : x + 4] = 255
        return img

    def close(self):
        self.closed = True


def test_run_writes_images_and_numbers(tmp_path):
    settings = preview_run.Settings(size=(64, 36))
    yard, minimal = str(PASS_DIR / "yard.usda"), str(PASS_DIR / "minimal.usda")
    files = [
        ("same.usda", "modified", minimal, minimal),
        ("moved.usda", "modified", minimal, yard),
        ("new.usda", "added", None, yard),
        ("gone.usda", "deleted", yard, None),
        ("lights.usda", "modified", str(PASS_DIR / "yard_lights.usda"), str(PASS_DIR / "yard_lights.usda")),
    ]
    doc = preview_run.run(files, str(tmp_path), settings, FakeRenderer)
    by = {f["path"]: f for f in doc["files"]}
    assert doc["status"] == "ok" and doc["renderer"]["name"] == "strata"
    assert by["same.usda"]["visible_change"] is False
    assert by["moved.usda"]["visible_change"] is True
    assert by["moved.usda"]["triangles"] == {"before": 12, "after": 48}
    view = by["moved.usda"]["views"][0]
    for key in ("before", "after", "diff", "strip"):
        assert (tmp_path / view[key]).is_file()
    assert "diff" not in by["new.usda"]["views"][0] and "before" not in by["new.usda"]["views"][0]
    assert "after" not in by["gone.usda"]["views"][0]
    assert by["lights.usda"]["status"] == "no_geometry"
    assert [v["name"] for v in by["same.usda"]["views"]] == ["three_quarter", "three_quarter_back"]


def test_run_reports_too_large(tmp_path):
    settings = preview_run.Settings(size=(32, 18), max_triangles=20)
    yard = str(PASS_DIR / "yard.usda")
    doc = preview_run.run([("y.usda", "modified", yard, yard)], str(tmp_path), settings, FakeRenderer)
    assert doc["files"][0]["status"] == "too_large"


def test_strata_start_failure_is_unavailable_not_an_error(tmp_path):
    def broken(*_):
        raise OSError("no EGL display")

    minimal = str(PASS_DIR / "minimal.usda")
    doc = preview_run.run([("m.usda", "added", None, minimal)], str(tmp_path), preview_run.Settings(), broken)
    assert doc["status"] == "unavailable" and "no EGL display" in doc["message"]
    assert "Preview unavailable" in previews.render(doc)


# --- config ---------------------------------------------------------------------------


def test_preview_key_needs_config_version_2():
    with pytest.raises(ConfigError, match="version: 2"):
        parse({"preview": {"enabled": True}})
    config = parse({"version": 2, "preview": {"enabled": True, "views": ["top"], "threshold": 2.5}})
    assert config.preview["views"] == ["top"]
    s = preview_run.Settings.from_config(config.preview)
    assert s.views == ["top"] and s.threshold == 2.5 and s.size == (640, 360)


@pytest.mark.parametrize(
    "preview,match",
    [
        ({"views": ["sideways"]}, "views"),
        ({"views": []}, "1 to 4"),
        ({"views": ["top", "top"]}, "twice"),
        ({"size": [640]}, "width, height"),
        ({"threshold": 0}, "threshold"),
        ({"max_fraction": 2}, "max_fraction"),
        ({"colour": "red"}, "unknown"),
    ],
)
def test_bad_preview_config(preview, match):
    with pytest.raises(ConfigError, match=match):
        parse({"version": 2, "preview": preview})


# --- comment section ------------------------------------------------------------------


def _doc(files, status="ok"):
    return {"version": 1, "status": status, "settings": {"threshold": 4.0}, "not_drawn": "opacity", "files": files}


def test_section_lists_visible_changes_and_collapses_the_rest():
    doc = _doc(
        [
            {"path": "a.usda", "change": "modified", "status": "rendered", "visible_change": True,
             "views": [{"name": "three_quarter", "pixels_over": 9, "fraction_over": 0.25, "max_delta_e": 70.0}]},
            {"path": "b.usda", "change": "modified", "status": "rendered", "visible_change": False, "views": []},
            {"path": "c.usda", "change": "modified", "status": "too_large", "message": "after: big", "views": []},
        ]
    )
    md = previews.render(doc, "https://x/run")
    assert "| `a.usda` | modified | 25.00 % | 70.0 | three quarter |" in md
    assert "No visible change in 1 file: `b.usda`." in md
    assert "too large: after: big" in md
    assert "[workflow run](https://x/run)" in md and "never fails the check" in md


def test_comment_body_appends_the_section():
    doc = _doc([{"path": "a.usda", "change": "added", "status": "rendered", "visible_change": True, "views": []}])
    body = comment.render_body(Report(files=["a.usda"], findings=[]), None, doc)
    assert body.startswith(comment.MARKER)
    assert "All checks pass" in body and previews.HEADING in body


def test_comment_with_previews_starts_a_comment_even_without_checked_files():
    calls = []

    def transport(method, url, headers, data):
        calls.append(method)
        return (200, {}, []) if method == "GET" else (201, {}, {"id": 5})

    ctx = comment.Context(token="t", repository="o/r", pr_number=1)
    doc = _doc([{"path": "a.usda", "change": "added", "status": "rendered", "visible_change": True, "views": []}])
    assert "created" in comment.post(Report(files=[], findings=[]), ctx, transport, previews=doc)
    assert calls == ["GET", "POST"]


# --- CLI ------------------------------------------------------------------------------


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True
    )


@pytest.fixture
def repo(tmp_path):
    """A git repo with the pass fixtures, then a commit that moves CrateB."""
    import shutil

    shutil.copytree(PASS_DIR, tmp_path / "assets", ignore=shutil.ignore_patterns("*.usdc", "*.usdz"))
    (tmp_path / ".usd-asset-gate.yml").write_text("version: 2\npreview:\n  enabled: true\n  size: [64, 36]\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    yard = tmp_path / "assets" / "yard.usda"
    yard.write_text(yard.read_text().replace("(2, 0, 0)", "(2, 0, 0.8)"))
    (tmp_path / "assets" / "minimal.usda").unlink()
    _git(tmp_path, "commit", "-qam", "change")
    return tmp_path


def test_cli_strata_unavailable_exits_0_and_cleans_up(repo, cli, monkeypatch):
    monkeypatch.setenv("STRATA_LIBRARY", str(repo / "no-such-libstrata.so"))
    out = repo / "out"
    code, stdout, _ = cli(
        "preview", "--changed-since", "HEAD~1", "--out", str(out), "--json", str(out / "p.json"), cwd=repo
    )
    assert code == 0
    assert "Preview unavailable" in stdout
    doc = json.loads((out / "p.json").read_text())
    assert doc["status"] == "unavailable"
    worktrees = subprocess.run(["git", "worktree", "list"], cwd=repo, capture_output=True, text=True).stdout
    assert len(worktrees.strip().splitlines()) == 1  # the temporary base worktree is gone


def test_cli_lists_modified_and_deleted_files(repo, cli, monkeypatch):
    seen = {}
    real_run = preview_run.run

    def fake_run(files, out_dir, settings, factory):
        for rel, kind, before, after in files:
            seen[rel] = (kind, before and os.path.isfile(before), after and os.path.isfile(after))
        return real_run([], out_dir, settings, factory)

    monkeypatch.setattr(preview_run, "run", fake_run)
    code, _, _ = cli("preview", "--changed-since", "HEAD~1", "--out", str(repo / "out"), cwd=repo)
    assert code == 0
    assert seen == {"assets/minimal.usda": ("deleted", True, None), "assets/yard.usda": ("modified", True, True)}


def test_cli_preview_is_opt_in(tmp_path, cli):
    code, stdout, _ = cli("preview", str(PASS_DIR / "minimal.usda"), "--out", str(tmp_path), cwd=tmp_path)
    assert code == 0 and "skipped" in stdout


def test_cli_usage_errors_exit_2(repo, cli):
    assert cli("preview", "--out", str(repo / "o"), cwd=repo)[0] == 2
    assert cli("preview", "--changed-since", "no-such-ref", "--out", str(repo / "o"), cwd=repo)[0] == 2


def test_check_never_imports_the_preview_package():
    code = (
        "import sys; from usd_asset_gate.cli import main; main(['check', %r]); "
        "bad = [m for m in sys.modules if m.startswith('usd_asset_gate.preview')]; "
        "print(bad)" % str(PASS_DIR / "minimal.usda")
    )
    import sys

    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip().splitlines()[-1] == "[]"
