"""The Strata wrapper's own logic: finding and checking the library, mapping binding
errors to StrataError, the sRGB table, and how render() drives the engine.

No libstrata needed: a fake library and engine stand in for the binding, so the
renderer's camera, lighting, mesh upload and readback are checked call by call.
tests/test_preview_render.py covers real renders when STRATA_LIBRARY is set.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys

import pytest

np = pytest.importorskip("numpy")

from usd_asset_gate._vendor import strata as _strata  # noqa: E402
from usd_asset_gate.preview import strata as strata_mod  # noqa: E402
from usd_asset_gate.preview.strata import Renderer, StrataError  # noqa: E402

# --- library_path / load --------------------------------------------------------------


def test_library_path_prefers_strata_library(monkeypatch):
    monkeypatch.setenv("STRATA_LIBRARY", "/opt/strata/libstrata.so")
    assert strata_mod.library_path() == "/opt/strata/libstrata.so"


@pytest.mark.parametrize("value", [None, ""])
def test_library_path_falls_back_to_the_loader_path(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("STRATA_LIBRARY", raising=False)
    else:
        monkeypatch.setenv("STRATA_LIBRARY", value)
    assert strata_mod.library_path() == "libstrata.so"


def test_load_missing_file_names_the_path_and_the_variable(tmp_path):
    missing = str(tmp_path / "no-such-libstrata.so")
    with pytest.raises(StrataError) as info:
        strata_mod.load(missing)
    assert str(info.value).startswith(f"cannot load Strata from {missing}: ")
    assert str(info.value).endswith("(set STRATA_LIBRARY)")
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_load_without_a_path_uses_strata_library(tmp_path, monkeypatch):
    missing = str(tmp_path / "from-env.so")
    monkeypatch.setenv("STRATA_LIBRARY", missing)
    with pytest.raises(StrataError, match="from-env.so"):
        strata_mod.load()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="loads the C library by its Linux name")
def test_load_a_library_that_is_not_strata_is_a_strata_error():
    libc = ctypes.util.find_library("c")
    assert libc
    with pytest.raises(StrataError) as info:
        strata_mod.load(libc)
    assert not isinstance(info.value, _strata.StrataError)
    assert str(info.value) == f"{libc}: {libc} does not export strata_abi_version; is it libstrata?"


class _AbiLibrary:
    def __init__(self, minor):
        self.abi_version, self.abi_minor = _strata.ABI_VERSION, minor


def _fake_loader(monkeypatch, minor):
    monkeypatch.setattr(strata_mod.C, "CDLL", lambda path: object())
    monkeypatch.setattr(strata_mod._strata, "Library", lambda cdll, path: _AbiLibrary(minor))


def test_load_rejects_an_abi_minor_below_the_mesh_call(monkeypatch):
    _fake_loader(monkeypatch, strata_mod.MIN_ABI_MINOR - 1)
    with pytest.raises(StrataError) as info:
        strata_mod.load("libstrata.so")
    assert str(info.value) == "Strata ABI 1.3 found; this preview needs 1.4+"


def test_load_accepts_exactly_the_minimum_abi_minor(monkeypatch):
    _fake_loader(monkeypatch, strata_mod.MIN_ABI_MINOR)
    assert strata_mod.load("libstrata.so").abi_minor == 4


# --- sRGB table -----------------------------------------------------------------------


def test_srgb_table_endpoints_and_known_values():
    table = strata_mod._srgb_table()
    assert table.dtype == np.uint8 and table.shape == (256,)
    assert {i: int(table[i]) for i in (0, 1, 2, 64, 128, 200, 254, 255)} == {
        0: 0, 1: 13, 2: 22, 64: 137, 128: 188, 200: 229, 254: 255, 255: 255
    }
    assert (np.diff(table.astype(int)) >= 0).all()
    assert (strata_mod._SRGB == table).all()


# --- Renderer -------------------------------------------------------------------------


class FakeLibrary:
    version = "0.6.0"

    def __init__(self, log, add_status=0):
        self.log, self.add_status, self.meshes = log, add_status, []

    def look_at(self, *args):
        self.log.append(("look_at", args))
        return "camera"

    def lighting_defaults(self, mode):
        self.log.append(("lighting_defaults", mode))
        return _strata.Lighting()

    def require(self, name):
        assert name == "strata_add_mesh"
        return self._add_mesh

    def _add_mesh(self, handle, mesh_ref, out_ref):
        mesh = mesh_ref._obj
        n = mesh.vertex_count
        self.log.append(("add_mesh", handle))
        self.meshes.append(
            {
                "positions": np.ctypeslib.as_array(mesh.positions, shape=(n * 3,)).copy(),
                "normals": np.ctypeslib.as_array(mesh.normals, shape=(n * 3,)).copy(),
                "colors": np.ctypeslib.as_array(mesh.colors, shape=(n * 3,)).copy(),
                "indices": np.ctypeslib.as_array(mesh.indices, shape=(mesh.index_count,)).copy(),
                "color": list(mesh.color),
            }
        )
        return self.add_status

    def check(self, status, what):
        if status:
            raise _strata.StrataError(status, f"{what} failed ({status})")


class FakeEngine:
    handle = "engine-handle"

    def __init__(self, lib, width, height, surface):
        self.created = (lib, width, height, surface)
        self.log = lib.log
        self.closed = 0
        self.pixels = None

    def gpu_description(self):
        return "llvmpipe"

    def set_camera(self, cam):
        self.log.append(("set_camera", cam))

    def set_lighting(self, light):
        self.log.append(("set_lighting", list(light.legacy_direction), light.legacy_ambient))

    def clear_meshes(self):
        self.log.append(("clear_meshes",))

    def begin_frame(self, clear):
        self.log.append(("begin_frame", clear))

    def end_frame(self):
        self.log.append(("end_frame",))

    def read_pixels(self):
        self.log.append(("read_pixels",))
        return self.pixels

    def close(self):
        self.closed += 1


@pytest.fixture
def fake(monkeypatch):
    log = []
    lib = FakeLibrary(log)
    monkeypatch.setattr(strata_mod, "load", lambda path=None: lib)
    monkeypatch.setattr(strata_mod._strata, "Engine", FakeEngine)
    return lib


def test_renderer_starts_one_headless_engine(fake):
    r = Renderer(320, 180)
    assert r._engine.created == (fake, 320, 180, _strata.SURFACE_HEADLESS)
    assert (r.width, r.height, r.version, r.gpu) == (320, 180, "0.6.0", "llvmpipe")


def test_renderer_maps_a_failed_create_to_strata_error(fake, monkeypatch):
    def refuse(lib, width, height, surface):
        raise _strata.StrataError(3, "no EGL display")

    monkeypatch.setattr(strata_mod._strata, "Engine", refuse)
    with pytest.raises(StrataError) as info:
        Renderer(320, 180)
    assert not isinstance(info.value, _strata.StrataError)
    assert str(info.value) == "strata_create failed: no EGL display"


def test_renderer_as_context_manager_closes_the_engine_once(fake):
    with Renderer(4, 4) as r:
        engine = r._engine
    assert engine.closed == 1


def test_call_maps_binding_errors_and_passes_results_through(fake):
    r = Renderer(4, 4)
    assert r._call(lambda a, b: a + b, 2, 3) == 5

    def fail():
        raise _strata.StrataError(2, "bad argument")

    with pytest.raises(StrataError) as info:
        r._call(fail)
    assert not isinstance(info.value, _strata.StrataError)
    assert str(info.value) == "bad argument"
    assert info.value.__cause__ is None and info.value.__suppress_context__


def _pixels(width, height):
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[..., 0] = 0
    rgba[..., 1] = 1
    rgba[..., 2] = 255
    rgba[..., 3] = 77  # alpha is dropped
    rgba[0, 0, :3] = (128, 64, 2)
    return rgba.tobytes(), width, height


def test_render_drives_the_engine_in_order_and_encodes_srgb(fake):
    r = Renderer(3, 2)
    r._engine.pixels = _pixels(3, 2)
    tri = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    quad = [[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 0, 1], [1, 1, 1], [0, 1, 1]]
    batches = [
        (tri, [[0, 0, 1]] * 3, [[0.5, 0.25, 0.125]] * 3),
        (np.array(quad, dtype=np.float64), np.zeros((6, 3)), np.ones((6, 3))),
    ]

    img = r.render(batches, eye=(1, 2, 3), target=(0, 0, 0), fov_y_degrees=40.0, near=0.1, far=50.0)

    assert [entry[0] for entry in fake.log] == [
        "look_at", "set_camera", "lighting_defaults", "set_lighting", "clear_meshes",
        "add_mesh", "add_mesh", "begin_frame", "end_frame", "read_pixels",
    ]
    look_at = fake.log[0][1]
    assert look_at == ((1.0, 2.0, 3.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), 40.0, 1.5, 0.1, 50.0)
    assert all(type(v) is float for v in look_at[0] + look_at[1])
    assert fake.log[1] == ("set_camera", "camera")
    assert fake.log[2] == ("lighting_defaults", _strata.LIGHTING_LEGACY)
    _, direction, ambient = fake.log[3]
    assert direction == pytest.approx([-0.4, -1.0, -0.3])
    assert ambient == pytest.approx(0.35)
    assert all(entry[1] == "engine-handle" for entry in fake.log if entry[0] == "add_mesh")
    assert fake.log[7] == ("begin_frame", (0.8, 0.82, 0.86, 1.0))

    first, second = fake.meshes
    assert first["positions"].tolist() == [0, 0, 0, 1, 0, 0, 0, 1, 0]
    assert first["colors"].tolist() == [0.5, 0.25, 0.125] * 3
    assert first["indices"].tolist() == [0, 1, 2]
    assert first["color"] == [1.0, 1.0, 1.0, 1.0]
    assert second["positions"].tolist() == np.array(quad).ravel().tolist()
    assert second["indices"].tolist() == [0, 1, 2, 3, 4, 5]

    assert img.dtype == np.uint8 and img.shape == (2, 3, 3)
    assert img[0, 0].tolist() == [188, 137, 22]
    assert img[1, 2].tolist() == [0, 13, 255]


def test_render_passes_custom_light_and_clear_colour(fake):
    r = Renderer(2, 2)
    r._engine.pixels = _pixels(2, 2)
    r.render([], (0, 0, 5), (0, 0, 0), 30.0, 1.0, 10.0, light_direction=(0, -1, 0), ambient=0.5, clear=(0, 0, 0))
    _, direction, ambient = next(e for e in fake.log if e[0] == "set_lighting")
    assert direction == [0.0, -1.0, 0.0] and ambient == 0.5
    assert ("begin_frame", (0, 0, 0, 1.0)) in fake.log
    assert not fake.meshes


def test_render_mesh_upload_failure_is_a_strata_error(fake):
    fake.add_status = 5
    r = Renderer(2, 2)
    with pytest.raises(StrataError, match=r"^strata_add_mesh failed \(5\)$"):
        r.render([([[0, 0, 0]] * 3, [[0, 0, 1]] * 3, [[1, 1, 1]] * 3)], (0, 0, 5), (0, 0, 0), 30.0, 1.0, 10.0)
    assert "begin_frame" not in [entry[0] for entry in fake.log]
