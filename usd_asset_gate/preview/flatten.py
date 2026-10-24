"""Turn a composed USD stage into world-space, flat-shaded, coloured triangles.

Uses ``usd-core`` only (``pxr.Usd``, ``UsdGeom``, ``UsdShade``, ``Gf``): the wheel has no
``GeomUtil`` or ``UsdImaging``, so the implicit gprims are tessellated here.

What is drawn (RFC 0028 §2): ``Mesh`` (fan-triangulated, control cage only),
``Cube``, ``Sphere``, ``Cylinder``, ``Cone``, ``Capsule`` and ``Plane``, prototypes of a
``PointInstancer`` at every instance, and everything reached through instance proxies.
Invisible prims and ``guide`` purpose are skipped; ``proxy`` purpose is skipped when the
stage has any ``render`` purpose geometry. Every other prim type is listed in
``FlatStage.unsupported`` so a report can say what was not drawn.

Colour comes from the ``displayColor`` primvar (any interpolation), else from a constant
``diffuseColor`` on a bound ``UsdPreviewSurface``, else mid grey. Vertices are never
shared between triangles, so each face keeps its own normal (flat shading), and Z-up
stages are rotated into Strata's Y-up frame ``(x, y, z) -> (x, z, -y)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

#: Colour of geometry with no displayColor and no constant preview-surface colour.
DEFAULT_COLOR = (0.5, 0.5, 0.5)
#: Strata's per-mesh vertex limit (``STRATA_MAX_MESH_VERTICES``, ABI 1.4).
MAX_MESH_VERTICES = 16_777_216
#: Strata's mesh-count limit (``STRATA_MAX_MESHES``, ABI 1.4).
MAX_MESHES = 256
DEFAULT_MAX_TRIANGLES = 2_000_000

_SEGMENTS = 24  # around the axis of round implicits
_RINGS = 12  # pole to pole on a sphere

_IMPLICITS = ("Cube", "Sphere", "Cylinder", "Cone", "Capsule", "Plane")


class TooLarge(Exception):
    """The stage has more triangles than the configured budget."""


@dataclass
class FlatStage:
    """Triangle soup in Strata's world frame: three rows per triangle."""

    positions: np.ndarray  # (3T, 3) float32
    normals: np.ndarray  # (3T, 3) float32
    colors: np.ndarray  # (3T, 3) float32, linear RGB
    unsupported: dict[str, int] = field(default_factory=dict)  # prim type -> count

    @property
    def triangle_count(self) -> int:
        return len(self.positions) // 3

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        if not len(self.positions):
            return None
        return self.positions.min(axis=0), self.positions.max(axis=0)

    def batches(self, max_vertices: int = MAX_MESH_VERTICES):
        """Split into meshes of at most ``max_vertices`` (whole triangles), for Strata."""
        step = max(3, max_vertices - max_vertices % 3)
        n = len(self.positions)
        out = [
            (self.positions[i : i + step], self.normals[i : i + step], self.colors[i : i + step])
            for i in range(0, n, step)
        ]
        if len(out) > MAX_MESHES:
            raise TooLarge(f"{len(out)} meshes needed, Strata holds {MAX_MESHES}")
        return out


# --- tessellation of implicit gprims (canonical axis +Z, centred at the origin) ----------


def _quad(a, b, c, d):
    return [a, b, c, a, c, d]


def _cube(size: float) -> np.ndarray:
    h = size / 2.0
    v = [(x * h, y * h, z * h) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    # index = 4x + 2y + z, each face counter-clockwise seen from outside
    faces = [
        (0, 1, 3, 2),  # -x
        (4, 6, 7, 5),  # +x
        (0, 4, 5, 1),  # -y
        (2, 3, 7, 6),  # +y
        (0, 2, 6, 4),  # -z
        (1, 5, 7, 3),  # +z
    ]
    idx = [i for f in faces for i in _quad(*f)]
    return np.array([v[i] for i in idx], dtype=np.float64)


def _ring(radius: float, z: float) -> list[tuple[float, float, float]]:
    return [
        (radius * math.cos(2 * math.pi * k / _SEGMENTS), radius * math.sin(2 * math.pi * k / _SEGMENTS), z)
        for k in range(_SEGMENTS)
    ]


def _lathe(profile: list[tuple[float, float]]) -> np.ndarray:
    """Revolve a (radius, z) profile, listed bottom to top, about +Z."""
    rings = [_ring(r, z) for r, z in profile]
    tris = []
    for lo, hi in zip(rings, rings[1:]):
        for k in range(_SEGMENTS):
            k1 = (k + 1) % _SEGMENTS
            a, b, c, d = lo[k], lo[k1], hi[k1], hi[k]
            if a != b:  # a ring of radius 0 (a pole or cap centre) gives a fan, not quads
                tris += [a, b, c]
            if c != d:
                tris += [a, c, d]
    return np.array(tris, dtype=np.float64).reshape(-1, 3)


def _sphere_profile(radius: float, z0: float = 0.0, upper: bool = True, lower: bool = True):
    out = []
    for i in range(_RINGS + 1):
        theta = -math.pi / 2 + math.pi * i / _RINGS
        if (theta < 0 and not lower) or (theta > 0 and not upper):
            continue
        r = 0.0 if i in (0, _RINGS) else radius * math.cos(theta)  # exact poles: no slivers
        out.append((r, z0 + radius * math.sin(theta)))
    return out


def _sphere(radius: float) -> np.ndarray:
    return _lathe(_sphere_profile(radius))


def _cylinder(radius: float, height: float) -> np.ndarray:
    h = height / 2
    return _lathe([(0.0, -h), (radius, -h), (radius, h), (0.0, h)])


def _cone(radius: float, height: float) -> np.ndarray:
    h = height / 2
    return _lathe([(0.0, -h), (radius, -h), (0.0, h)])


def _capsule(radius: float, height: float) -> np.ndarray:
    h = height / 2
    return _lathe(_sphere_profile(radius, -h, upper=False) + _sphere_profile(radius, h, lower=False))


def _plane(width: float, length: float) -> np.ndarray:
    w, ln = width / 2, length / 2
    corners = [(-w, -ln, 0.0), (w, -ln, 0.0), (w, ln, 0.0), (-w, ln, 0.0)]
    return np.array([corners[i] for i in _quad(0, 1, 2, 3)], dtype=np.float64)


def _to_axis(tris: np.ndarray, axis: str) -> np.ndarray:
    """Rotate canonical +Z geometry so its axis is ``axis`` (cyclic permutations keep winding)."""
    if axis == "X":
        return tris[:, [2, 0, 1]]
    if axis == "Y":
        return tris[:, [1, 2, 0]]
    return tris


def implicit_triangles(prim) -> np.ndarray | None:
    """Triangle corners (3T, 3) in the prim's local space, or None if not an implicit."""
    from pxr import UsdGeom

    t = prim.GetTypeName()
    if t == "Cube":
        return _cube(float(UsdGeom.Cube(prim).GetSizeAttr().Get()))
    if t == "Sphere":
        return _sphere(float(UsdGeom.Sphere(prim).GetRadiusAttr().Get()))
    if t == "Plane":
        s = UsdGeom.Plane(prim)
        tris = _plane(float(s.GetWidthAttr().Get()), float(s.GetLengthAttr().Get()))
        axis = s.GetAxisAttr().Get()
        # A plane's width runs along X unless its normal is X, where it runs along Z.
        if axis == "X":
            return tris[:, [2, 1, 0]] * np.array([1.0, 1.0, -1.0])
        if axis == "Y":
            return tris[:, [0, 2, 1]] * np.array([1.0, 1.0, -1.0])
        return tris
    schema = {"Cylinder": UsdGeom.Cylinder, "Cone": UsdGeom.Cone, "Capsule": UsdGeom.Capsule}.get(t)
    if schema is None:
        return None
    s = schema(prim)
    r, h = float(s.GetRadiusAttr().Get()), float(s.GetHeightAttr().Get())
    tris = {"Cylinder": _cylinder, "Cone": _cone, "Capsule": _capsule}[t](r, h)
    return _to_axis(tris, s.GetAxisAttr().Get())


# --- meshes ------------------------------------------------------------------------------


def mesh_triangles(prim):
    """(corners (3T, 3) local, point index per corner, face index per corner, face-vertex
    index per corner) for a ``UsdGeom.Mesh``, fan-triangulated."""
    from pxr import UsdGeom

    mesh = UsdGeom.Mesh(prim)
    points = np.asarray(mesh.GetPointsAttr().Get() or [], dtype=np.float64).reshape(-1, 3)
    counts = list(mesh.GetFaceVertexCountsAttr().Get() or [])
    indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get() or [], dtype=np.int64)
    left = mesh.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded
    fv, face = [], []
    k = 0
    for f, c in enumerate(counts):
        for j in range(1, c - 1):
            tri = [k, k + j, k + j + 1]
            if left:
                tri = [tri[0], tri[2], tri[1]]
            fv += tri
            face += [f, f, f]
        k += c
    fv = np.asarray(fv, dtype=np.int64)
    if not len(fv) or k > len(indices):
        return None
    pidx = indices[fv]
    if len(pidx) and (pidx.min() < 0 or pidx.max() >= len(points)):
        return None
    return points[pidx], pidx, np.asarray(face, dtype=np.int64), fv


# --- colour ------------------------------------------------------------------------------


def _material_color(prim):
    from pxr import UsdShade

    material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    if not material:
        return None
    output = material.GetSurfaceOutput()
    sources = output.GetConnectedSources()[0] if output else []
    for source in sources:
        shader = UsdShade.Shader(source.source.GetPrim())
        if not shader or shader.GetIdAttr().Get() != "UsdPreviewSurface":
            continue
        inp = shader.GetInput("diffuseColor")
        if inp and not inp.HasConnectedSource():
            value = inp.Get()
            if value is not None:
                return tuple(float(c) for c in value)
    return None


def corner_colors(prim, n_corners, pidx=None, face=None, fv=None) -> np.ndarray:
    """Per-corner linear RGB for a gprim (corner-index arrays are given for meshes)."""
    from pxr import UsdGeom

    # Constant primvars authored on an ancestor (an instance root, say) are inherited.
    pv = UsdGeom.PrimvarsAPI(prim).FindPrimvarWithInheritance("displayColor")
    if pv and pv.HasValue():
        values = pv.ComputeFlattened()
        if values is not None and len(values):
            arr = np.asarray(values, dtype=np.float32).reshape(-1, 3)
            interp = pv.GetInterpolation()
            src = None
            if interp in ("vertex", "varying") and pidx is not None:
                src = pidx
            elif interp == "uniform" and face is not None:
                src = face
            elif interp == "faceVarying" and fv is not None:
                src = fv
            if src is not None and len(src) and src.max() < len(arr):
                return arr[src]
            return np.repeat(arr[:1], n_corners, axis=0)
    color = _material_color(prim) or DEFAULT_COLOR
    return np.tile(np.asarray(color, dtype=np.float32), (n_corners, 1))


# --- traversal ---------------------------------------------------------------------------


def _xform(corners: np.ndarray, matrix) -> np.ndarray:
    """Apply a Gf row-vector 4x4 matrix to (N, 3) points."""
    m = np.asarray(matrix, dtype=np.float64).reshape(4, 4)
    return corners @ m[:3, :3] + m[3, :3]


def _swap_winding(corners: np.ndarray) -> np.ndarray:
    return corners.reshape(-1, 3, 3)[:, [0, 2, 1]].reshape(-1, 3)


class _Collector:
    def __init__(self, stage, max_triangles: int):
        from pxr import UsdGeom

        self.stage = stage
        self.max_triangles = max_triangles
        self.z_up = UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
        self.cache = UsdGeom.XformCache()
        self.pos: list[np.ndarray] = []
        self.col: list[np.ndarray] = []
        self.triangles = 0
        self.unsupported: dict[str, int] = {}
        self.skip_proxy = False

    def add(self, corners: np.ndarray, colors: np.ndarray, double_sided: bool):
        if not len(corners):
            return
        if double_sided:
            corners = np.concatenate([corners, _swap_winding(corners)])
            colors = np.concatenate([colors, _swap_winding(colors)])
        self.triangles += len(corners) // 3
        if self.triangles > self.max_triangles:
            raise TooLarge(f"more than {self.max_triangles:,} triangles")
        self.pos.append(corners)
        self.col.append(colors)

    def gprim(self, prim, matrix):
        """Local triangles and colours of one gprim, transformed by ``matrix``."""
        from pxr import UsdGeom

        t = prim.GetTypeName()
        if prim.IsA(UsdGeom.Mesh):
            got = mesh_triangles(prim)
            if got is None:
                return
            corners, pidx, face, fv = got
            colors = corner_colors(prim, len(corners), pidx, face, fv)
        elif t in _IMPLICITS:
            corners = implicit_triangles(prim)
            colors = corner_colors(prim, len(corners))
        else:
            self.unsupported[t] = self.unsupported.get(t, 0) + 1
            return
        double = bool(UsdGeom.Gprim(prim).GetDoubleSidedAttr().Get())
        world = _xform(corners, matrix)
        if np.linalg.det(np.asarray(matrix, dtype=np.float64).reshape(4, 4)[:3, :3]) < 0:
            # A mirroring transform turns the winding inside out; swap two corners back.
            world, colors = _swap_winding(world), _swap_winding(colors)
        self.add(world, colors, double)

    def drawable(self, prim) -> bool:
        from pxr import UsdGeom

        img = UsdGeom.Imageable(prim)
        purpose = img.ComputePurpose()
        if purpose == UsdGeom.Tokens.guide:
            return False
        if purpose == UsdGeom.Tokens.proxy and self.skip_proxy:
            return False
        return True

    def instancer(self, prim):
        from pxr import Gf, Usd, UsdGeom

        pi = UsdGeom.PointInstancer(prim)
        targets = pi.GetPrototypesRel().GetTargets()
        indices = pi.GetProtoIndicesAttr().Get() or []
        if not targets or not len(indices):
            return
        time = self.stage.GetStartTimeCode() if self.stage.HasAuthoredTimeCodeRange() else Usd.TimeCode.Default()
        xforms = pi.ComputeInstanceTransformsAtTime(time, time, UsdGeom.PointInstancer.ExcludeProtoXform)
        mask = pi.ComputeMaskAtTime(time)
        world = self.cache.GetLocalToWorldTransform(prim)
        protos = []  # per prototype: list of (gprim, matrix relative to the instancer's space)
        for path in targets:
            root = self.stage.GetPrimAtPath(path)
            entries = []
            if root:
                parent = self.cache.GetLocalToWorldTransform(root.GetParent())
                inv = parent.GetInverse()
                for sub in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
                    if not sub.IsA(UsdGeom.Gprim) or not self.drawable(sub):
                        continue
                    if UsdGeom.Imageable(sub).ComputeVisibility() == UsdGeom.Tokens.invisible:
                        continue
                    entries.append((sub, self.cache.GetLocalToWorldTransform(sub) * inv))
            protos.append(entries)
        for i, (proto, xf) in enumerate(zip(indices, xforms)):
            if mask and not mask[i]:
                continue
            if not 0 <= proto < len(protos):
                continue
            inst = Gf.Matrix4d(xf) * world
            for sub, rel in protos[proto]:
                self.gprim(sub, rel * inst)

    def run(self):
        from pxr import Usd, UsdGeom

        predicate = Usd.TraverseInstanceProxies()
        self.skip_proxy = any(
            p.IsA(UsdGeom.Gprim) and UsdGeom.Imageable(p).ComputePurpose() == UsdGeom.Tokens.render
            for p in Usd.PrimRange(self.stage.GetPseudoRoot(), predicate)
        )
        it = iter(Usd.PrimRange(self.stage.GetPseudoRoot(), predicate))
        for prim in it:
            if not prim.IsA(UsdGeom.Imageable):
                continue
            if UsdGeom.Imageable(prim).ComputeVisibility() == UsdGeom.Tokens.invisible:
                it.PruneChildren()
                continue
            if prim.IsA(UsdGeom.PointInstancer):
                it.PruneChildren()
                self.instancer(prim)
                continue
            if not prim.IsA(UsdGeom.Gprim) or not self.drawable(prim):
                continue
            self.gprim(prim, self.cache.GetLocalToWorldTransform(prim))


def flatten_stage(stage, max_triangles: int = DEFAULT_MAX_TRIANGLES) -> FlatStage:
    """Flatten an open stage. Raises :class:`TooLarge` over the budget."""
    c = _Collector(stage, max_triangles)
    c.run()
    if c.pos:
        pos = np.concatenate(c.pos)
        col = np.concatenate(c.col).astype(np.float32)
    else:
        pos = np.zeros((0, 3))
        col = np.zeros((0, 3), dtype=np.float32)
    if c.z_up:
        pos = np.stack([pos[:, 0], pos[:, 2], -pos[:, 1]], axis=1)
    tri = pos.reshape(-1, 3, 3)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-20)
    normals = np.repeat(n, 3, axis=0)
    return FlatStage(
        positions=pos.astype(np.float32),
        normals=normals.astype(np.float32),
        colors=np.clip(col, 0.0, 1.0),
        unsupported=dict(sorted(c.unsupported.items())),
    )


def flatten_file(path: str, max_triangles: int = DEFAULT_MAX_TRIANGLES) -> FlatStage:
    from pxr import Usd

    stage = Usd.Stage.Open(path, Usd.Stage.LoadAll)
    if stage is None:
        raise ValueError(f"cannot open {path} as a USD stage")
    return flatten_stage(stage, max_triangles)
