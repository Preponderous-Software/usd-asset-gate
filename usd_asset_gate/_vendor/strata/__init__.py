"""Python binding for the Strata engine's C ABI (include/strata/strata.h).

Pure ctypes, no compiled code. A host loads a libstrata it chooses (a local
build or a released library pinned by SHA-256), the binding checks every
struct layout against the library, and a small Engine class wraps the calls:

    import strata

    lib = strata.load()  # STRATA_LIBRARY or STRATA_NATIVE_DIR; or strata.load_release("0.4.0")
    with strata.Engine(lib, 320, 180) as engine:  # headless by default
        engine.set_camera(lib.look_at((0, 3, 8), (0, 0, 0), fov_y_degrees=60, aspect=320 / 180))
        engine.set_lighting(lib.lighting_defaults(strata.LIGHTING_LEGACY))
        engine.begin_frame(clear_color=(0.45, 0.65, 0.9, 1))
        engine.submit_primitives([strata.RenderItem(strata.PRIMITIVE_CUBE, (0, 0, 0), (1, 1, 1), (1, 0, 0, 1))])
        engine.end_frame()
        rgba, width, height = engine.read_pixels()

The struct classes, constants and the raw functions (lib.raw.strata_*) are
the C ABI one to one; see strata.h for what each does. Desktop only (Linux
x86-64 and Windows x64): Python in a browser (Pyodide) is out of scope.
"""

from __future__ import annotations

import ctypes
import os
from array import array
from typing import Iterable, Optional, Sequence, Tuple

from . import _abi
from ._abi import *  # noqa: F401,F403  (constants and structs are the public API)
from ._abi import (
    ABI_MINOR,
    ABI_VERSION,
    FUNCTIONS,
    OK,
    Camera,
    EngineDesc,
    Event,
    FrameDesc,
    FrameStats,
    LineVertex,
    Lighting,
    LogFn,
    Mesh,
    Point,
    PointLight,
    RayHit,
    Rect,
    RenderItem,
    SolarPosition,
    StrataAbiError,
    StrataError,
    TerrainTile,
    Text,
    TexturedMesh,
    bind,
    check_layouts,
    STRUCTS,
)
from ._loader import KNOWN_RELEASES, StrataLoadError, fetch_release, open_library, resolve_library_path

__version__ = "0.6.0"

__all__ = _abi.__all__ + [
    "Engine",
    "KNOWN_RELEASES",
    "Library",
    "StrataLoadError",
    "fetch_release",
    "load",
    "load_release",
]


def _floats(values: Iterable[float], count: int):
    values = tuple(values)
    if len(values) != count:
        raise ValueError("expected %d numbers, got %d" % (count, len(values)))
    return (ctypes.c_float * count)(*values)


def _array(cls, items):
    """A ctypes array of cls from a ctypes array, a sequence of cls, or None."""
    if items is None:
        return None, 0
    if isinstance(items, ctypes.Array):
        return items, len(items)
    items = list(items)
    return (cls * len(items))(*items), len(items)


class Library:
    """A loaded, layout-checked libstrata.

    raw is the ctypes.CDLL with argtypes set; the methods here are the calls
    that need no engine.
    """

    def __init__(self, cdll: ctypes.CDLL, path: Optional[str] = None):
        self.raw = cdll
        self.path = path
        self._missing = bind(cdll)
        for required in ("strata_abi_version", "strata_struct_size", "strata_struct_field_offset", "strata_create"):
            if required in self._missing:
                raise StrataAbiError("%s does not export %s; is it libstrata?" % (path or "the library", required))
        self.abi_version = int(cdll.strata_abi_version())
        if self.abi_version != ABI_VERSION:
            raise StrataAbiError(
                "the library has ABI %d; this binding speaks ABI %d" % (self.abi_version, ABI_VERSION)
            )
        # strata_abi_minor arrived in 1.1: a library without it is 1.0.
        self.abi_minor = 0 if "strata_abi_minor" in self._missing else int(cdll.strata_abi_minor())
        self.version = cdll.strata_version_string().decode("utf-8")
        self.structs_checked = check_layouts(cdll, self.abi_minor)

    def __repr__(self) -> str:
        return "<strata.Library %s ABI %d.%d at %s>" % (self.version, self.abi_version, self.abi_minor, self.path)

    def has(self, name: str) -> bool:
        """Whether the library exports a C ABI function (older libraries lack newer ones)."""
        return name in FUNCTIONS and name not in self._missing

    def require(self, name: str):
        """The raw function, or StrataError naming the minor it needs."""
        if name in self._missing:
            raise StrataError(
                7,  # STRATA_ERROR_UNSUPPORTED
                "%s needs Strata ABI 1.%d; the library is %d.%d"
                % (name, self._missing[name], self.abi_version, self.abi_minor),
            )
        return getattr(self.raw, name)

    def last_error(self) -> str:
        return self.raw.strata_last_error().decode("utf-8", "replace")

    def check(self, status: int, what: str) -> None:
        if status != OK:
            raise StrataError(status, "%s failed (%d): %s" % (what, status, self.last_error()))

    def struct_size(self, struct_id: int) -> int:
        return int(self.raw.strata_struct_size(struct_id))

    def struct_field_offset(self, struct_id: int, field_id: int) -> int:
        return int(self.raw.strata_struct_field_offset(struct_id, field_id))

    def look_at(
        self,
        eye: Sequence[float],
        target: Sequence[float],
        up: Sequence[float] = (0.0, 1.0, 0.0),
        fov_y_degrees: float = 60.0,
        aspect: float = 16.0 / 9.0,
        near_plane: float = 0.1,
        far_plane: float = 300.0,
    ) -> Camera:
        """strata_camera_look_at: a right-handed view and an OpenGL perspective."""
        camera = Camera()
        self.check(
            self.raw.strata_camera_look_at(
                _floats(eye, 3), _floats(target, 3), _floats(up, 3), fov_y_degrees, aspect, near_plane, far_plane,
                ctypes.byref(camera),
            ),
            "strata_camera_look_at",
        )
        return camera

    def lighting_defaults(self, mode: int) -> Lighting:
        lighting = Lighting()
        self.check(self.raw.strata_lighting_defaults(mode, ctypes.byref(lighting)), "strata_lighting_defaults")
        return lighting

    def solar_position(self, unix_seconds_utc: float, latitude_degrees: float, longitude_degrees: float) -> SolarPosition:
        """ABI 1.6."""
        position = SolarPosition()
        self.check(
            self.require("strata_solar_position")(
                unix_seconds_utc, latitude_degrees, longitude_degrees, ctypes.byref(position)
            ),
            "strata_solar_position",
        )
        return position


def load(path: Optional[os.PathLike] = None) -> Library:
    """Loads and checks a libstrata: path, else STRATA_LIBRARY, else STRATA_NATIVE_DIR."""
    resolved = resolve_library_path(path)
    return Library(open_library(resolved), str(resolved))


def load_release(version: str, sha256: Optional[str] = None, cache_dir: Optional[os.PathLike] = None) -> Library:
    """Fetches a released library (verified against sha256), then loads it."""
    path = fetch_release(version, sha256, cache_dir)
    return Library(open_library(path), str(path))


class Engine:
    """One strata_create'd engine. Main thread only, like the C ABI.

    Use it as a context manager, or call close(). Methods raise StrataError
    with strata_last_error() text when a call fails.
    """

    def __init__(
        self,
        lib: Library,
        width: int = 640,
        height: int = 360,
        surface: int = 1,  # STRATA_SURFACE_HEADLESS
        flags: int = 0,
        title: Optional[str] = None,
    ):
        self.lib = lib
        self._raw = lib.raw
        desc = EngineDesc()
        desc.struct_size = ctypes.sizeof(EngineDesc)
        desc.abi_version = ABI_VERSION
        desc.surface = surface
        desc.width = width
        desc.height = height
        desc.flags = flags
        desc.title = title.encode("utf-8") if title else None
        handle = ctypes.c_void_p()
        lib.check(self._raw.strata_create(ctypes.byref(desc), ctypes.byref(handle)), "strata_create")
        self._handle = handle

    # -- lifetime

    def close(self) -> None:
        if self._handle:
            self._raw.strata_destroy(self._handle)
            self._handle = ctypes.c_void_p()

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self):
        # Engines must be destroyed on the thread that made them; close()
        # explicitly when that matters. This is a last resort.
        try:
            self.close()
        except Exception:
            pass

    @property
    def handle(self) -> ctypes.c_void_p:
        """The StrataEngine* for raw calls (lib.raw.strata_*)."""
        if not self._handle:
            raise StrataError(6, "the engine is closed")  # STRATA_ERROR_INVALID_STATE
        return self._handle

    def _call(self, name: str, *args) -> None:
        self.lib.check(self.lib.require(name)(self.handle, *args), name)

    # -- queries

    def gpu_description(self) -> str:
        return self._raw.strata_gpu_description(self.handle).decode("utf-8", "replace")

    def drawable_size(self) -> Tuple[int, int]:
        width, height = ctypes.c_int32(), ctypes.c_int32()
        self._call("strata_get_drawable_size", ctypes.byref(width), ctypes.byref(height))
        return width.value, height.value

    def resize(self, width: int, height: int) -> None:
        self._call("strata_resize", width, height)

    def poll_event(self) -> Optional[Event]:
        event = Event()
        return event if self._raw.strata_poll_event(self.handle, ctypes.byref(event)) else None

    def push_event(self, event: Event) -> None:
        """ABI 1.12. Queues a copy of an event for poll_event, on any surface."""
        self._call("strata_push_event", ctypes.byref(event))

    def frame_seconds(self) -> float:
        """ABI 1.12. Seconds to advance this frame: measured, or a stepped driver's step (docs/DRIVE.md)."""
        seconds = ctypes.c_double()
        self._call("strata_frame_seconds", ctypes.byref(seconds))
        return seconds.value

    def driven(self) -> bool:
        """ABI 1.12. True if a harness drove this engine (STRATA_DRIVE)."""
        return bool(self.lib.require("strata_is_driven")(self.handle))

    def frame_stats(self) -> FrameStats:
        stats = FrameStats()
        self._call("strata_get_frame_stats", ctypes.byref(stats))
        return stats

    # -- scene state

    def set_camera(self, camera: Camera) -> None:
        self._call("strata_set_camera", ctypes.byref(camera))

    def set_lighting(self, lighting: Lighting) -> None:
        self._call("strata_set_lighting", ctypes.byref(lighting))

    def set_sun_direction(self, to_sun: Optional[Sequence[float]]) -> None:
        """ABI 1.6. None goes back to the lighting's time of day."""
        self._call("strata_set_sun_direction", _floats(to_sun, 3) if to_sun is not None else None)

    def set_point_lights(self, lights: Iterable[PointLight]) -> None:
        """ABI 1.1."""
        arr, count = _array(PointLight, lights)
        self._call("strata_set_point_lights", arr, count)

    def set_terrain(self, tile: Optional[TerrainTile]) -> None:
        """ABI 1.3. None removes the terrain."""
        self._call("strata_set_terrain", ctypes.byref(tile) if tile is not None else None)

    def add_mesh(
        self,
        positions: Sequence[float],
        indices: Sequence[int],
        color: Sequence[float] = (1.0, 1.0, 1.0, 1.0),
        normals: Optional[Sequence[float]] = None,
        colors: Optional[Sequence[float]] = None,
        uvs: Optional[Sequence[float]] = None,
        texture: int = 0,
        flags: int = 0,
    ) -> int:
        """ABI 1.4 (1.7 with uvs, a texture or flags). Returns the mesh id."""
        pos = array("f", positions)
        idx = array("I", indices)
        if len(pos) % 3:
            raise ValueError("positions must be xyz triples")
        keep = []

        def pointer(data, ctype, typecode):
            if data is None:
                return None
            buf = array(typecode, data)
            keep.append(buf)
            return ctypes.cast((ctype * len(buf)).from_buffer(buf), ctypes.POINTER(ctype))

        mesh = Mesh()
        mesh.positions = pointer(pos, ctypes.c_float, "f")
        mesh.normals = pointer(normals, ctypes.c_float, "f")
        mesh.colors = pointer(colors, ctypes.c_float, "f")
        mesh.indices = pointer(idx, ctypes.c_uint32, "I")
        mesh.vertex_count = len(pos) // 3
        mesh.index_count = len(idx)
        mesh.color = _floats(color, 4)
        out = ctypes.c_uint32()
        if uvs is None and not texture and not flags:
            self._call("strata_add_mesh", ctypes.byref(mesh), ctypes.byref(out))
        else:
            textured = TexturedMesh()
            textured.mesh = mesh
            textured.uvs = pointer(uvs, ctypes.c_float, "f")
            textured.texture = texture
            textured.flags = flags
            self._call("strata_add_textured_mesh", ctypes.byref(textured), ctypes.byref(out))
        return out.value

    def remove_mesh(self, mesh_id: int) -> None:
        self._call("strata_remove_mesh", mesh_id)

    def clear_meshes(self) -> None:
        self._call("strata_clear_meshes")

    def set_mesh_transform(self, mesh_id: int, model: Optional[Sequence[float]]) -> None:
        """ABI 1.7. 16 floats, column-major; None restores the identity."""
        self._call("strata_set_mesh_transform", mesh_id, _floats(model, 16) if model is not None else None)

    def create_texture_rgba8(self, width: int, height: int, rgba: bytes, flags: int = 0) -> int:
        """ABI 1.7. rgba is width * height * 4 bytes, top row first. Returns the texture id."""
        data = (ctypes.c_uint8 * len(rgba)).from_buffer_copy(rgba)
        out = ctypes.c_uint32()
        self._call("strata_create_texture_rgba8", width, height, data, flags, ctypes.byref(out))
        return out.value

    def update_texture_rgba8(self, texture: int, x: int, y: int, width: int, height: int, rgba: bytes) -> None:
        """ABI 1.8."""
        data = (ctypes.c_uint8 * len(rgba)).from_buffer_copy(rgba)
        self._call("strata_update_texture_rgba8", texture, x, y, width, height, data)

    def destroy_texture(self, texture: int) -> None:
        self._call("strata_destroy_texture", texture)

    def raycast_meshes(
        self, origin: Sequence[float], direction: Sequence[float], max_distance: float = float("inf")
    ) -> RayHit:
        """ABI 1.8. hit.mesh_id is 0 when nothing was hit."""
        hit = RayHit()
        self._call("strata_raycast_meshes", _floats(origin, 3), _floats(direction, 3), max_distance, ctypes.byref(hit))
        return hit

    def set_relative_mouse_mode(self, enabled: bool) -> None:
        """ABI 1.8. Window surface only."""
        self._call("strata_set_relative_mouse_mode", 1 if enabled else 0)

    # -- frames

    def begin_frame(
        self,
        clear_color: Sequence[float] = (0.0, 0.0, 0.0, 1.0),
        clear_flags: int = 3,  # STRATA_CLEAR_COLOR | STRATA_CLEAR_DEPTH
    ) -> None:
        desc = FrameDesc()
        desc.clear_flags = clear_flags
        desc.clear_color = _floats(clear_color, 4)
        self._call("strata_begin_frame", ctypes.byref(desc))

    def submit_primitives(self, items) -> None:
        """A ctypes array of RenderItem (passed without a copy) or any iterable of them."""
        arr, count = _array(RenderItem, items)
        self._call("strata_submit_primitives", arr, count)

    def submit_lines(self, vertices, flags: int = 0) -> None:
        """ABI 1.5. An even number of LineVertex; each pair is one segment.
        flags (LINES_ON_TOP, ABI 1.11) go through strata_submit_lines_ex."""
        arr, count = _array(LineVertex, vertices)
        if flags:
            self._call("strata_submit_lines_ex", arr, count, flags)
        else:
            self._call("strata_submit_lines", arr, count)

    def submit_rects(self, rects) -> None:
        """ABI 1.11. Filled screen rectangles, drawn before this frame's text."""
        arr, count = _array(Rect, rects)
        self._call("strata_submit_rects", arr, count)

    def submit_points(self, points) -> None:
        """ABI 1.5."""
        arr, count = _array(Point, points)
        self._call("strata_submit_points", arr, count)

    def submit_text(self, texts) -> None:
        """ABI 1.5. Text.utf8 must be bytes (NUL-terminated by ctypes)."""
        arr, count = _array(Text, texts)
        self._call("strata_submit_text", arr, count)

    def end_frame(self) -> None:
        self._call("strata_end_frame")

    def present(self) -> None:
        self._call("strata_present")

    # -- readback

    def read_pixels(self) -> Tuple[bytes, int, int]:
        """The last frame as RGBA8 bytes, top row first, with its width and height."""
        width, height = self.drawable_size()
        buf = (ctypes.c_uint8 * (width * height * 4))()
        w, h = ctypes.c_int32(), ctypes.c_int32()
        self._call("strata_read_pixels_rgba8", buf, len(buf), ctypes.byref(w), ctypes.byref(h))
        return bytes(buf)[: w.value * h.value * 4], w.value, h.value

    def read_depth(self) -> Tuple[array, int, int]:
        """ABI 1.9. Planar view distance per pixel (array of float32), top row first;
        +inf where nothing was drawn. Headless surfaces only."""
        width, height = self.drawable_size()
        out = array("f", bytes(4 * width * height))
        buf = (ctypes.c_float * len(out)).from_buffer(out)
        w, h = ctypes.c_int32(), ctypes.c_int32()
        self._call("strata_read_depth_f32", buf, 4 * len(out), ctypes.byref(w), ctypes.byref(h))
        del buf
        return out, w.value, h.value

    def save_screenshot_png(self, path: os.PathLike) -> None:
        self._call("strata_save_screenshot_png", os.fsencode(path) if os.name != "nt" else str(path).encode("utf-8"))
