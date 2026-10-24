"""A minimal ``ctypes`` binding to Strata's versioned C ABI (``include/strata/strata.h``).

Only what a headless preview needs: create a headless engine (EGL surfaceless), set a
camera and the LEGACY lighting model, upload static meshes, render one frame and read it
back as RGBA8. Every struct this module declares is checked against
``strata_struct_size`` when the library is loaded, so an ABI mismatch fails loudly instead
of corrupting memory.

The library is found at ``STRATA_LIBRARY`` (a path to ``libstrata.so``), else as
``libstrata.so`` on the loader path. It needs ABI 1.4 or later (``strata_add_mesh``).
Headless rendering on a machine without a GPU needs Mesa's llvmpipe, for example
``LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe``.
"""

from __future__ import annotations

import ctypes as C
import os

import numpy as np

ABI_MAJOR = 1
MIN_ABI_MINOR = 4  # strata_add_mesh / StrataMesh

SURFACE_HEADLESS = 1
LIGHTING_LEGACY = 0
CLEAR_COLOR = 1
CLEAR_DEPTH = 2

_F3, _F4, _F16 = C.c_float * 3, C.c_float * 4, C.c_float * 16


class StrataError(RuntimeError):
    """Strata could not be loaded or started, or a call failed."""


class EngineDesc(C.Structure):
    _fields_ = [
        ("struct_size", C.c_uint32),
        ("abi_version", C.c_uint32),
        ("surface", C.c_int32),
        ("width", C.c_int32),
        ("height", C.c_int32),
        ("flags", C.c_uint32),
        ("title", C.c_char_p),
        ("get_proc_address", C.c_void_p),
        ("get_proc_address_user", C.c_void_p),
    ]


class Camera(C.Structure):
    _fields_ = [
        ("view", _F16),
        ("projection", _F16),
        ("eye", _F3),
        ("near_plane", C.c_float),
        ("far_plane", C.c_float),
    ]


class Lighting(C.Structure):
    _fields_ = [
        ("mode", C.c_int32),
        ("legacy_direction", _F3),
        ("legacy_ambient", C.c_float),
        ("time_of_day", C.c_float),
        ("latitude_degrees", C.c_float),
        ("cascade_count", C.c_int32),
        ("shadow_map_size", C.c_int32),
        ("shadow_distance", C.c_float),
        ("split_lambda", C.c_float),
        ("fog", C.c_int32),
    ]


class FrameDesc(C.Structure):
    _fields_ = [("clear_flags", C.c_uint32), ("clear_color", _F4)]


class Mesh(C.Structure):
    _fields_ = [
        ("positions", C.POINTER(C.c_float)),
        ("normals", C.POINTER(C.c_float)),
        ("colors", C.POINTER(C.c_float)),
        ("indices", C.POINTER(C.c_uint32)),
        ("vertex_count", C.c_int32),
        ("index_count", C.c_int32),
        ("color", _F4),
    ]


#: STRATA_STRUCT_* ids from strata.h.
_STRUCT_IDS = {1: EngineDesc, 2: Camera, 3: Lighting, 4: FrameDesc, 9: Mesh}


def library_path() -> str:
    return os.environ.get("STRATA_LIBRARY") or "libstrata.so"


def load(path: str | None = None):
    """Load and check the library; raises :class:`StrataError` with the reason."""
    path = path or library_path()
    try:
        lib = C.CDLL(path)
    except OSError as exc:
        raise StrataError(f"cannot load Strata from {path}: {exc} (set STRATA_LIBRARY)") from None
    lib.strata_version_string.restype = C.c_char_p
    lib.strata_last_error.restype = C.c_char_p
    lib.strata_gpu_description.restype = C.c_char_p
    lib.strata_gpu_description.argtypes = [C.c_void_p]
    for name in ("strata_abi_version", "strata_abi_minor"):
        getattr(lib, name).restype = C.c_uint32
    major, minor = lib.strata_abi_version(), lib.strata_abi_minor()
    if major != ABI_MAJOR or minor < MIN_ABI_MINOR:
        raise StrataError(f"Strata ABI {major}.{minor} found; this binding needs {ABI_MAJOR}.{MIN_ABI_MINOR}+")
    for sid, struct in _STRUCT_IDS.items():
        have = lib.strata_struct_size(sid)
        if have != C.sizeof(struct):
            raise StrataError(f"Strata struct {struct.__name__} is {have} bytes, binding expects {C.sizeof(struct)}")
    return lib


def _fptr(a: np.ndarray):
    return a.ctypes.data_as(C.POINTER(C.c_float))


def _srgb_table() -> np.ndarray:
    c = np.arange(256) / 255.0
    s = np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)
    return np.round(np.clip(s, 0, 1) * 255).astype(np.uint8)


_SRGB = _srgb_table()


class Renderer:
    """One headless Strata engine, reused for every image of a run."""

    def __init__(self, width: int, height: int, path: str | None = None):
        self.lib = load(path)
        self.width, self.height = width, height
        self.engine = C.c_void_p()
        desc = EngineDesc(C.sizeof(EngineDesc), ABI_MAJOR, SURFACE_HEADLESS, width, height, 0, None, None, None)
        self._ok(self.lib.strata_create(C.byref(desc), C.byref(self.engine)), "strata_create")
        self._buf = (C.c_uint8 * (width * height * 4))()

    @property
    def version(self) -> str:
        return self.lib.strata_version_string().decode()

    @property
    def gpu(self) -> str:
        return (self.lib.strata_gpu_description(self.engine) or b"").decode()

    def _ok(self, status: int, what: str) -> None:
        if status != 0:
            detail = (self.lib.strata_last_error() or b"").decode()
            raise StrataError(f"{what} failed (status {status}): {detail}")

    def close(self) -> None:
        if self.engine:
            self.lib.strata_destroy(self.engine)
            self.engine = C.c_void_p()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def render(
        self,
        batches,
        eye,
        target,
        fov_y_degrees: float,
        near: float,
        far: float,
        light_direction=(-0.4, -1.0, -0.3),
        ambient: float = 0.35,
        clear=(0.8, 0.82, 0.86),
    ) -> np.ndarray:
        """Draw ``batches`` of (positions, normals, colours) and return (H, W, 3) sRGB uint8.

        Strata writes linear colour with no output transform, so the readback is
        encoded to sRGB here; the ΔE comparison expects sRGB input.
        """
        lib, eng = self.lib, self.engine
        cam = Camera()
        up = _F3(0.0, 1.0, 0.0)
        self._ok(
            lib.strata_camera_look_at(
                _F3(*eye), _F3(*target), up, C.c_float(fov_y_degrees),
                C.c_float(self.width / self.height), C.c_float(near), C.c_float(far), C.byref(cam),
            ),
            "strata_camera_look_at",
        )
        self._ok(lib.strata_set_camera(eng, C.byref(cam)), "strata_set_camera")
        light = Lighting()
        self._ok(lib.strata_lighting_defaults(LIGHTING_LEGACY, C.byref(light)), "strata_lighting_defaults")
        light.legacy_direction = _F3(*light_direction)
        light.legacy_ambient = ambient
        self._ok(lib.strata_set_lighting(eng, C.byref(light)), "strata_set_lighting")
        self._ok(lib.strata_clear_meshes(eng), "strata_clear_meshes")
        keep = []
        for pos, nrm, col in batches:
            pos = np.ascontiguousarray(pos, dtype=np.float32)
            nrm = np.ascontiguousarray(nrm, dtype=np.float32)
            col = np.ascontiguousarray(col, dtype=np.float32)
            idx = np.arange(len(pos), dtype=np.uint32)
            keep += [pos, nrm, col, idx]
            mesh = Mesh(_fptr(pos), _fptr(nrm), _fptr(col), idx.ctypes.data_as(C.POINTER(C.c_uint32)),
                        len(pos), len(idx), _F4(1.0, 1.0, 1.0, 1.0))
            out = C.c_uint32()
            self._ok(lib.strata_add_mesh(eng, C.byref(mesh), C.byref(out)), "strata_add_mesh")
        frame = FrameDesc(CLEAR_COLOR | CLEAR_DEPTH, _F4(*clear, 1.0))
        self._ok(lib.strata_begin_frame(eng, C.byref(frame)), "strata_begin_frame")
        self._ok(lib.strata_end_frame(eng), "strata_end_frame")
        w, h = C.c_int32(), C.c_int32()
        self._ok(
            lib.strata_read_pixels_rgba8(eng, self._buf, len(self._buf), C.byref(w), C.byref(h)),
            "strata_read_pixels_rgba8",
        )
        img = np.frombuffer(self._buf, dtype=np.uint8).reshape(h.value, w.value, 4)[..., :3]
        del keep
        return _SRGB[img]
