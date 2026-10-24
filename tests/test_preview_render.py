"""Real renders through Strata (its official Python binding over libstrata.so).

Skipped unless STRATA_LIBRARY points at a Strata release library; CI's
"preview (Strata headless)" job sets it and runs on Mesa llvmpipe
(LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe, EGL surfaceless).
"""

from __future__ import annotations

import os

import pytest

np = pytest.importorskip("numpy")

from conftest import PASS_DIR  # noqa: E402
from usd_asset_gate.preview import compare as cmp  # noqa: E402
from usd_asset_gate.preview import run as preview_run  # noqa: E402

if os.environ.get("USD_ASSET_GATE_REQUIRE_STRATA") == "1" and not os.environ.get("STRATA_LIBRARY"):
    raise RuntimeError("USD_ASSET_GATE_REQUIRE_STRATA=1 but STRATA_LIBRARY is not set")
pytestmark = pytest.mark.skipif(not os.environ.get("STRATA_LIBRARY"), reason="STRATA_LIBRARY is not set")


@pytest.fixture(scope="module")
def renderer():
    from usd_asset_gate.preview.strata import Renderer

    r = Renderer(320, 180)
    yield r
    r.close()


def _render(renderer, flat, framing=None, view="three_quarter"):
    centre, radius = framing or preview_run.frame_bounds([flat])
    eye, target, near, far = preview_run.camera_for(view, centre, radius, 320 / 180)
    return renderer.render(flat.batches(), eye, target, preview_run.FOV_Y_DEGREES, near, far)


def test_binding_reports_version_and_driver(renderer):
    assert renderer.version.count(".") == 2
    assert renderer.gpu


def test_yard_renders_something_and_is_deterministic(renderer):
    from usd_asset_gate.preview.flatten import flatten_file

    flat = flatten_file(str(PASS_DIR / "yard.usda"))
    a = _render(renderer, flat)
    b = _render(renderer, flat)
    assert a.shape == (180, 320, 3)
    background = a[0, 0]
    assert ((a != background).any(axis=-1)).mean() > 0.05  # the crates cover part of the frame
    assert (a == b).all()


def test_moving_a_crate_is_a_visible_change(renderer, tmp_path):
    from usd_asset_gate.preview.flatten import flatten_file

    moved = tmp_path / "yard.usda"
    text = (PASS_DIR / "yard.usda").read_text().replace("(2, 0, 0)", "(2, 0, 0.8)")
    for name in ("crate.usda", "yard_lights.usda"):
        (tmp_path / name).write_text((PASS_DIR / name).read_text())
    (tmp_path / "pieces").mkdir()
    (tmp_path / "pieces" / "lid.usda").write_text((PASS_DIR / "pieces" / "lid.usda").read_text())
    moved.write_text(text)
    before = flatten_file(str(PASS_DIR / "yard.usda"))
    after = flatten_file(str(moved))
    framing = preview_run.frame_bounds([before, after])
    result, _ = cmp.compare(_render(renderer, before, framing), _render(renderer, after, framing))
    assert result.visible_change and result.max_delta_e > 10
    same, _ = cmp.compare(_render(renderer, before, framing), _render(renderer, before, framing))
    assert same.pixels_over == 0


def test_every_view_renders(renderer):
    from usd_asset_gate.preview.flatten import flatten_file

    flat = flatten_file(str(PASS_DIR / "crate.usda"))
    for view in preview_run.VIEW_DIRECTIONS:
        img = _render(renderer, flat, view=view)
        assert (img != img[0, 0]).any(axis=-1).any(), view


def test_binding_is_strata_s_own_and_checked_every_struct(renderer):
    from usd_asset_gate._vendor import STRATA_BINDING

    assert renderer.lib.structs_checked > 0
    assert renderer.lib.version == STRATA_BINDING["tag"].lstrip("v")  # CI pins the matching library


def test_delta_e_port_matches_strata_imgcmp(renderer, tmp_path):
    """compare.py is Strata's src/image/compare.cpp ported to numpy: same numbers as
    ``strata-imgcmp --json`` (STRATA_IMGCMP, from the release tarball) on a real change."""
    import json
    import subprocess

    imgcmp = os.environ.get("STRATA_IMGCMP")
    if not imgcmp:
        if os.environ.get("USD_ASSET_GATE_REQUIRE_STRATA") == "1":
            pytest.fail("STRATA_IMGCMP is not set")
        pytest.skip("STRATA_IMGCMP is not set")
    from usd_asset_gate.preview.flatten import flatten_file

    moved = tmp_path / "yard.usda"
    for name in ("crate.usda", "yard_lights.usda"):
        (tmp_path / name).write_text((PASS_DIR / name).read_text())
    (tmp_path / "pieces").mkdir()
    (tmp_path / "pieces" / "lid.usda").write_text((PASS_DIR / "pieces" / "lid.usda").read_text())
    moved.write_text((PASS_DIR / "yard.usda").read_text().replace("(2, 0, 0)", "(2, 0, 0.8)"))
    before, after = flatten_file(str(PASS_DIR / "yard.usda")), flatten_file(str(moved))
    framing = preview_run.frame_bounds([before, after])
    a, b = _render(renderer, before, framing), _render(renderer, after, framing)
    cmp.write_png(str(tmp_path / "a.png"), a)
    cmp.write_png(str(tmp_path / "b.png"), b)
    ours, _ = cmp.compare(a, b)
    proc = subprocess.run(
        [imgcmp, str(tmp_path / "a.png"), str(tmp_path / "b.png"), "--json"], capture_output=True, text=True
    )
    assert proc.returncode == 1, proc.stderr  # 1: over the max fraction
    theirs = json.loads(proc.stdout)
    assert ours.pixels == theirs["pixels"] and ours.pixels_over == theirs["pixels_over"]
    assert ours.fraction_over == pytest.approx(theirs["fraction_over"], abs=1e-8)
    assert ours.max_delta_e == pytest.approx(theirs["max_delta_e"], abs=1e-5)
    assert ours.mean_delta_e == pytest.approx(theirs["mean_delta_e"], abs=1e-5)
    assert ours.visible_change == (not theirs["passed"])
