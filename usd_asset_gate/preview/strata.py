"""Strata, for a headless preview, through Strata's official Python binding.

The binding (``usd_asset_gate/_vendor/strata``, vendored unmodified from the Strata release
named in ``usd_asset_gate._vendor.STRATA_BINDING``) declares the C ABI
(``include/strata/strata.h``) and checks the ABI major version and every struct layout
against the loaded library, so an ABI mismatch fails loudly instead of corrupting memory.
This module only adds what a preview needs on top: a headless engine (EGL surfaceless),
a camera and the LEGACY lighting model, static meshes uploaded straight from NumPy, one
frame read back as RGBA8 and encoded to sRGB.

The library is found at ``STRATA_LIBRARY`` (a path to ``libstrata.so``), else as
``libstrata.so`` on the loader path. It needs ABI 1.4 or later (``strata_add_mesh``).
Headless rendering on a machine without a GPU needs Mesa's llvmpipe, for example
``LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe``.
"""

from __future__ import annotations

import ctypes as C
import os

import numpy as np

from .._vendor import strata as _strata

MIN_ABI_MINOR = 4  # strata_add_mesh / StrataMesh


class StrataError(RuntimeError):
    """Strata could not be loaded or started, or a call failed."""


def library_path() -> str:
    return os.environ.get("STRATA_LIBRARY") or "libstrata.so"


def load(path: str | None = None) -> _strata.Library:
    """Load and check the library; raises :class:`StrataError` with the reason."""
    path = path or library_path()
    try:
        cdll = C.CDLL(path)
    except OSError as exc:
        raise StrataError(f"cannot load Strata from {path}: {exc} (set STRATA_LIBRARY)") from None
    try:
        lib = _strata.Library(cdll, path)
    except _strata.StrataError as exc:
        raise StrataError(f"{path}: {exc}") from None
    if lib.abi_minor < MIN_ABI_MINOR:
        raise StrataError(
            f"Strata ABI {lib.abi_version}.{lib.abi_minor} found; this preview needs "
            f"{_strata.ABI_VERSION}.{MIN_ABI_MINOR}+"
        )
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
        try:
            self._engine = _strata.Engine(self.lib, width, height, surface=_strata.SURFACE_HEADLESS)
        except _strata.StrataError as exc:
            raise StrataError(f"strata_create failed: {exc}") from None

    @property
    def version(self) -> str:
        return self.lib.version

    @property
    def gpu(self) -> str:
        return self._engine.gpu_description()

    def _call(self, fn, *args):
        try:
            return fn(*args)
        except _strata.StrataError as exc:
            raise StrataError(str(exc)) from None

    def close(self) -> None:
        self._engine.close()

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
        lib, eng = self.lib, self._engine
        cam = self._call(
            lib.look_at,
            tuple(float(v) for v in eye),
            tuple(float(v) for v in target),
            (0.0, 1.0, 0.0),
            fov_y_degrees,
            self.width / self.height,
            near,
            far,
        )
        self._call(eng.set_camera, cam)
        light = self._call(lib.lighting_defaults, _strata.LIGHTING_LEGACY)
        light.legacy_direction[:] = [float(v) for v in light_direction]
        light.legacy_ambient = ambient
        self._call(eng.set_lighting, light)
        self._call(eng.clear_meshes)
        add_mesh = lib.require("strata_add_mesh")
        keep = []
        for pos, nrm, col in batches:
            pos = np.ascontiguousarray(pos, dtype=np.float32)
            nrm = np.ascontiguousarray(nrm, dtype=np.float32)
            col = np.ascontiguousarray(col, dtype=np.float32)
            idx = np.arange(len(pos), dtype=np.uint32)
            keep += [pos, nrm, col, idx]
            mesh = _strata.Mesh()
            mesh.positions, mesh.normals, mesh.colors = _fptr(pos), _fptr(nrm), _fptr(col)
            mesh.indices = idx.ctypes.data_as(C.POINTER(C.c_uint32))
            mesh.vertex_count, mesh.index_count = len(pos), len(idx)
            mesh.color[:] = [1.0, 1.0, 1.0, 1.0]
            out = C.c_uint32()
            self._call(lib.check, add_mesh(eng.handle, C.byref(mesh), C.byref(out)), "strata_add_mesh")
        self._call(eng.begin_frame, (*clear, 1.0))
        self._call(eng.end_frame)
        rgba, w, h = self._call(eng.read_pixels)
        del keep
        img = np.frombuffer(rgba, dtype=np.uint8).reshape(h, w, 4)[..., :3]
        return _SRGB[img]
