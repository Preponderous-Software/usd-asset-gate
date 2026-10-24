"""USD → triangles for the advisory preview (RFC 0028 §2). No Strata needed."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt  # noqa: E402

from conftest import PASS_DIR  # noqa: E402
from usd_asset_gate.preview import flatten  # noqa: E402
from usd_asset_gate.preview.flatten import TooLarge, flatten_file, flatten_stage  # noqa: E402

# Pinned triangle counts for every expected-pass fixture. A change here means the
# flattener changed what it draws: review the preview images before updating.
PASS_TRIANGLES = {
    "crate.usda": 24,  # body cube + lid cube
    "crate.usdc": 24,  # built by tests/fixtures/build.py
    "crate.usdz": 24,
    "minimal.usda": 12,
    "pieces/lid.usda": 12,
    "yard.usda": 48,  # two instanced crates, each with its referenced lid
    "yard_lights.usda": 0,  # a light only
}


def test_every_pass_fixture_is_pinned():
    on_disk = sorted(p.relative_to(PASS_DIR).as_posix() for p in PASS_DIR.rglob("*.usd*"))
    assert sorted(PASS_TRIANGLES) == on_disk


@pytest.mark.parametrize("name,count", sorted(PASS_TRIANGLES.items()))
def test_pass_fixture_triangle_counts(name, count):
    flat = flatten_file(str(PASS_DIR / name))
    assert flat.triangle_count == count
    assert flat.unsupported == {}
    assert flat.positions.shape == flat.normals.shape == flat.colors.shape == (3 * count, 3)


def test_yard_instances_resolve_through_instance_proxies():
    flat = flatten_file(str(PASS_DIR / "yard.usda"))
    lo, hi = flat.bounds()
    assert np.allclose(lo, [-0.5, -0.5, -0.5]) and np.allclose(hi, [2.5, 0.5, 0.5])


def _stage(up="Y"):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, up)
    return stage


def _outward(flat, centre=(0, 0, 0)):
    tri = flat.positions.reshape(-1, 3, 3).astype(np.float64)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    return bool(((n * (tri.mean(axis=1) - np.asarray(centre))).sum(axis=1) > 0).all())


@pytest.mark.parametrize(
    "kind,count",
    [("Cube", 12), ("Sphere", 528), ("Cylinder", 96), ("Cone", 48), ("Capsule", 576)],
)
@pytest.mark.parametrize("axis", ["X", "Y", "Z"])
def test_implicit_gprims_are_closed_and_wound_outwards(kind, count, axis):
    stage = _stage()
    prim = stage.DefinePrim("/G", kind)
    if prim.HasAttribute("axis"):
        prim.GetAttribute("axis").Set(axis)
    flat = flatten_stage(stage)
    assert flat.triangle_count == count
    assert _outward(flat)


def test_cylinder_axis_and_size():
    stage = _stage()
    cyl = UsdGeom.Cylinder.Define(stage, "/C")
    cyl.CreateAxisAttr("X")
    cyl.CreateRadiusAttr(0.5)
    cyl.CreateHeightAttr(4.0)
    lo, hi = flatten_stage(stage).bounds()
    assert np.allclose(lo, [-2, -0.5, -0.5], atol=1e-6) and np.allclose(hi, [2, 0.5, 0.5], atol=1e-6)


@pytest.mark.parametrize("axis,normal", [("X", 0), ("Y", 1), ("Z", 2)])
def test_plane_faces_its_axis_and_is_double_sided(axis, normal):
    stage = _stage()
    plane = UsdGeom.Plane.Define(stage, "/P")
    plane.CreateAxisAttr(axis)
    flat = flatten_stage(stage)
    assert flat.triangle_count == 4  # two triangles, drawn from both sides
    assert np.allclose(flat.positions[:, normal], 0.0)
    assert sorted({round(float(n), 6) for n in flat.normals[:, normal]}) == [-1.0, 1.0]


def _quad_mesh(stage, path="/M", orientation=None):
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0)])
    mesh.CreateFaceVertexCountsAttr([4, 3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3, 1, 4, 2])
    if orientation:
        mesh.CreateOrientationAttr(orientation)
    return mesh


def test_mesh_polygons_are_fan_triangulated_with_face_normals():
    stage = _stage()
    _quad_mesh(stage)
    flat = flatten_stage(stage)
    assert flat.triangle_count == 3
    assert np.allclose(flat.normals, [0, 0, 1])


def test_left_handed_meshes_flip_winding():
    stage = _stage()
    _quad_mesh(stage, orientation=UsdGeom.Tokens.leftHanded)
    assert np.allclose(flatten_stage(stage).normals, [0, 0, -1])


def test_mirroring_transform_keeps_faces_outward():
    stage = _stage()
    cube = UsdGeom.Cube.Define(stage, "/C")
    cube.AddScaleOp().Set(Gf.Vec3f(-1, 1, 1))
    assert _outward(flatten_stage(stage))


def test_z_up_stages_are_rotated_to_y_up():
    stage = _stage("Z")
    cube = UsdGeom.Cube.Define(stage, "/C")
    cube.AddTranslateOp().Set(Gf.Vec3d(0, 0, 5))  # 5 up in Z-up
    lo, hi = flatten_stage(stage).bounds()
    assert np.allclose((lo + hi) / 2, [0, 5, 0])


def test_display_color_interpolations():
    stage = _stage()
    mesh = _quad_mesh(stage)
    pv = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
        "displayColor", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.uniform
    )
    pv.Set([(1, 0, 0), (0, 0, 1)])
    colors = flatten_stage(stage).colors
    assert np.allclose(colors[:6], [1, 0, 0]) and np.allclose(colors[6:], [0, 0, 1])
    pv.SetInterpolation(UsdGeom.Tokens.vertex)
    pv.Set([(i / 4, 0, 0) for i in range(5)])
    colors = flatten_stage(stage).colors
    assert np.allclose(colors[:3, 0], [0, 0.25, 0.5])  # corners 0, 1, 2 of the quad
    pv.SetInterpolation(UsdGeom.Tokens.faceVarying)
    pv.Set([(0, i / 7, 0) for i in range(7)])
    colors = flatten_stage(stage).colors
    assert np.allclose(colors[6:, 1], [4 / 7, 5 / 7, 6 / 7])  # the triangle face


def test_inherited_display_color_from_an_ancestor():
    stage = _stage()
    root = UsdGeom.Xform.Define(stage, "/Root")
    UsdGeom.PrimvarsAPI(root).CreatePrimvar("displayColor", Sdf.ValueTypeNames.Color3fArray).Set([(0, 1, 0)])
    UsdGeom.Cube.Define(stage, "/Root/C")
    assert np.allclose(flatten_stage(stage).colors, [0, 1, 0])


def test_preview_surface_diffuse_color_is_the_fallback():
    stage = _stage()
    cube = UsdGeom.Cube.Define(stage, "/C")
    material = UsdShade.Material.Define(stage, "/M")
    shader = UsdShade.Shader.Define(stage, "/M/S")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set((0.2, 0.4, 0.6))
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(material)
    assert np.allclose(flatten_stage(stage).colors, [0.2, 0.4, 0.6])


def test_no_colour_means_mid_grey():
    stage = _stage()
    UsdGeom.Cube.Define(stage, "/C")
    assert np.allclose(flatten_stage(stage).colors, flatten.DEFAULT_COLOR)


def test_invisible_and_guide_prims_are_skipped():
    stage = _stage()
    UsdGeom.Cube.Define(stage, "/Shown")
    UsdGeom.Cube.Define(stage, "/Hidden").CreateVisibilityAttr(UsdGeom.Tokens.invisible)
    UsdGeom.Cube.Define(stage, "/Guide").CreatePurposeAttr(UsdGeom.Tokens.guide)
    hidden_parent = UsdGeom.Xform.Define(stage, "/HiddenParent")
    hidden_parent.CreateVisibilityAttr(UsdGeom.Tokens.invisible)
    UsdGeom.Cube.Define(stage, "/HiddenParent/C")
    assert flatten_stage(stage).triangle_count == 12


def test_proxy_purpose_is_skipped_only_when_render_geometry_exists():
    stage = _stage()
    UsdGeom.Cube.Define(stage, "/Proxy").CreatePurposeAttr(UsdGeom.Tokens.proxy)
    assert flatten_stage(stage).triangle_count == 12
    UsdGeom.Sphere.Define(stage, "/Render").CreatePurposeAttr(UsdGeom.Tokens.render)
    assert flatten_stage(stage).triangle_count == 528


def test_point_instancer_draws_each_instance():
    stage = _stage()
    pi = UsdGeom.PointInstancer.Define(stage, "/PI")
    proto = UsdGeom.Cube.Define(stage, "/PI/Protos/Box")
    proto.CreateSizeAttr(1.0)
    pi.CreatePrototypesRel().AddTarget(proto.GetPath())
    pi.CreateProtoIndicesAttr([0, 0, 0])
    pi.CreatePositionsAttr(Vt.Vec3fArray([(0, 0, 0), (10, 0, 0), (0, 0, 10)]))
    pi.CreateScalesAttr(Vt.Vec3fArray([(1, 1, 1), (2, 2, 2), (1, 1, 1)]))
    flat = flatten_stage(stage)
    assert flat.triangle_count == 36  # the prototype itself is not drawn on its own
    lo, hi = flat.bounds()
    assert np.allclose(lo, [-0.5, -1, -1]) and np.allclose(hi, [11, 1, 10.5])  # 2x box at x=10


def test_unsupported_gprims_are_counted():
    stage = _stage()
    UsdGeom.BasisCurves.Define(stage, "/Curve")
    UsdGeom.Points.Define(stage, "/Pts")
    UsdGeom.Cube.Define(stage, "/C")
    flat = flatten_stage(stage)
    assert flat.triangle_count == 12
    assert flat.unsupported == {"BasisCurves": 1, "Points": 1}


def test_triangle_budget():
    stage = _stage()
    UsdGeom.Sphere.Define(stage, "/S")
    with pytest.raises(TooLarge):
        flatten_stage(stage, max_triangles=100)


def test_batches_split_on_whole_triangles():
    stage = _stage()
    UsdGeom.Cube.Define(stage, "/C")
    batches = flatten_stage(stage).batches(max_vertices=10)
    assert [len(b[0]) for b in batches] == [9, 9, 9, 9]
    assert sum(len(b[0]) for b in batches) == 36


def test_empty_stage_has_no_bounds():
    assert flatten_stage(_stage()).bounds() is None
