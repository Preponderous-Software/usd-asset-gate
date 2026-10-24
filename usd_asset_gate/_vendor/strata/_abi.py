"""The C ABI of include/strata/strata.h, declared with ctypes.

Every struct here mirrors one in strata.h field for field, in declaration
order, so a ctypes field's index is the field id strata_struct_field_offset
takes. check_layouts() compares each struct with the loaded library before
anything crosses the boundary, the same guard as the C# binding's layout
tests: a field added on one side fails loudly instead of corrupting memory.

Each struct and function names the ABI minor it arrived in. A library older
than that minor does not have it: functions are bound only when the library
exports them, and Engine methods raise StrataError naming the minor they need.
"""

from __future__ import annotations

import ctypes
from ctypes import (
    POINTER,
    Structure,
    c_char,
    c_char_p,
    c_double,
    c_float,
    c_int32,
    c_uint8,
    c_uint32,
    c_void_p,
)

#: The major version this binding was written against (STRATA_ABI_VERSION).
ABI_VERSION = 1
#: The newest minor whose additions this binding declares (STRATA_ABI_MINOR).
ABI_MINOR = 12

# ---------------------------------------------------------------- status

OK = 0
ERROR_INVALID_ARGUMENT = 1
ERROR_ABI_MISMATCH = 2
ERROR_PLATFORM = 3
ERROR_GPU = 4
ERROR_IO = 5
ERROR_INVALID_STATE = 6
ERROR_UNSUPPORTED = 7
ERROR_INTERNAL = 8

# ---------------------------------------------------------------- enums and flags

PRIMITIVE_CUBE = 0
PRIMITIVE_CAPSULE = 1
PRIMITIVE_CYLINDER = 2
PRIMITIVE_SPHERE = 3
PRIMITIVE_KIND_COUNT = 4

SURFACE_WINDOW = 0
SURFACE_HEADLESS = 1
SURFACE_HOST_CONTEXT = 2

FLAG_VSYNC = 1 << 0
FLAG_DEBUG_CONTEXT = 1 << 1
FLAG_HIDDEN_WINDOW = 1 << 2
FLAG_GPU_TIMING = 1 << 3

LIGHTING_LEGACY = 0
LIGHTING_SUN = 1
LIGHTING_SPACE = 2  # ABI 1.5

MAX_POINT_LIGHTS = 32  # ABI 1.1
POINT_LIGHT_CAST_SHADOWS = 0x1  # ABI 1.2
MAX_SHADOWED_POINT_LIGHTS = 4  # ABI 1.2

MAX_MESHES = 256  # ABI 1.4
MAX_MESH_VERTICES = 16777216  # ABI 1.4

MAX_TEXTURES = 256  # ABI 1.7
MAX_TEXTURE_SIZE = 8192  # ABI 1.7
TEXTURE_MIPMAPS = 0x1  # ABI 1.7
TEXTURE_CLAMP_U = 0x2  # ABI 1.7
TEXTURE_CLAMP_V = 0x4  # ABI 1.7
TEXTURE_NEAREST = 0x8  # ABI 1.10
MESH_UNLIT = 0x1  # ABI 1.7
MESH_TRANSLUCENT = 0x2  # ABI 1.11

POINT_SOFT = 0x1  # ABI 1.5
POINT_ON_TOP = 0x2  # ABI 1.11
LINES_ON_TOP = 0x1  # ABI 1.11, strata_submit_lines_ex
TEXT_SHADOW = 0x1  # ABI 1.5

CLEAR_COLOR = 1 << 0
CLEAR_DEPTH = 1 << 1
CLEAR_SKY = 1 << 2

EVENT_NONE = 0
EVENT_QUIT = 1
EVENT_KEY_DOWN = 2
EVENT_KEY_UP = 3
EVENT_MOUSE_MOTION = 4
EVENT_MOUSE_BUTTON_DOWN = 5
EVENT_MOUSE_BUTTON_UP = 6
EVENT_MOUSE_WHEEL = 7
EVENT_RESIZED = 8
EVENT_TEXT_INPUT = 9

# ---------------------------------------------------------------- structs

float3 = c_float * 3
float4 = c_float * 4
float16 = c_float * 16


class RenderItem(Structure):
    """StrataRenderItem: 44 bytes, Beyond Nations' RenderItem layout."""

    STRUCT_ID = 0
    ABI_MINOR = 0
    _fields_ = [("kind", c_int32), ("position", float3), ("scale", float3), ("color", float4)]


GetProcAddressFn = ctypes.CFUNCTYPE(c_void_p, c_char_p, c_void_p)


class EngineDesc(Structure):
    STRUCT_ID = 1
    ABI_MINOR = 0
    _fields_ = [
        ("struct_size", c_uint32),
        ("abi_version", c_uint32),
        ("surface", c_int32),
        ("width", c_int32),
        ("height", c_int32),
        ("flags", c_uint32),
        ("title", c_char_p),
        ("get_proc_address", GetProcAddressFn),
        ("get_proc_address_user", c_void_p),
    ]


class Camera(Structure):
    STRUCT_ID = 2
    ABI_MINOR = 0
    _fields_ = [
        ("view", float16),
        ("projection", float16),
        ("eye", float3),
        ("near_plane", c_float),
        ("far_plane", c_float),
    ]


class Lighting(Structure):
    STRUCT_ID = 3
    ABI_MINOR = 0
    _fields_ = [
        ("mode", c_int32),
        ("legacy_direction", float3),
        ("legacy_ambient", c_float),
        ("time_of_day", c_float),
        ("latitude_degrees", c_float),
        ("cascade_count", c_int32),
        ("shadow_map_size", c_int32),
        ("shadow_distance", c_float),
        ("split_lambda", c_float),
        ("fog", c_int32),
    ]


class FrameDesc(Structure):
    STRUCT_ID = 4
    ABI_MINOR = 0
    _fields_ = [("clear_flags", c_uint32), ("clear_color", float4)]


class FrameStats(Structure):
    STRUCT_ID = 5
    ABI_MINOR = 0
    _fields_ = [
        ("draw_calls", c_int32),
        ("instances", c_int32),
        ("instances_per_kind", c_int32 * 4),
        ("items_rejected", c_int32),
        ("shadow_cascades", c_int32),
        ("cpu_ms_shadow", c_float),
        ("cpu_ms_opaque", c_float),
        ("cpu_ms_sky", c_float),
        ("cpu_ms_frame", c_float),
        ("gpu_finish_ms", c_float),
    ]


class Event(Structure):
    STRUCT_ID = 6
    ABI_MINOR = 0
    _fields_ = [
        ("type", c_int32),
        ("scancode", c_int32),
        ("modifiers", c_int32),
        ("repeat", c_int32),
        ("button", c_int32),
        ("width", c_int32),
        ("height", c_int32),
        ("x", c_float),
        ("y", c_float),
        ("dx", c_float),
        ("dy", c_float),
        ("text", c_char * 32),
    ]


class PointLight(Structure):
    """ABI 1.1. 36 bytes."""

    STRUCT_ID = 7
    ABI_MINOR = 1
    _fields_ = [
        ("position", float3),
        ("range", c_float),
        ("color", float3),
        ("intensity", c_float),
        ("flags", c_uint32),
    ]


class TerrainTile(Structure):
    """ABI 1.3."""

    STRUCT_ID = 8
    ABI_MINOR = 3
    _fields_ = [
        ("origin", float3),
        ("cell_size", c_float),
        ("samples_x", c_int32),
        ("samples_z", c_int32),
        ("heights", POINTER(c_float)),
        ("colors", POINTER(c_float)),
        ("color", float4),
    ]


class Mesh(Structure):
    """ABI 1.4."""

    STRUCT_ID = 9
    ABI_MINOR = 4
    _fields_ = [
        ("positions", POINTER(c_float)),
        ("normals", POINTER(c_float)),
        ("colors", POINTER(c_float)),
        ("indices", POINTER(c_uint32)),
        ("vertex_count", c_int32),
        ("index_count", c_int32),
        ("color", float4),
    ]


class LineVertex(Structure):
    """ABI 1.5. 28 bytes."""

    STRUCT_ID = 10
    ABI_MINOR = 5
    _fields_ = [("position", float3), ("color", float4)]


class Point(Structure):
    """ABI 1.5. 40 bytes."""

    STRUCT_ID = 11
    ABI_MINOR = 5
    _fields_ = [
        ("position", float3),
        ("size_pixels", c_float),
        ("world_diameter", c_float),
        ("color", float4),
        ("flags", c_uint32),
    ]


class Text(Structure):
    """ABI 1.5."""

    STRUCT_ID = 12
    ABI_MINOR = 5
    _fields_ = [
        ("x", c_float),
        ("y", c_float),
        ("pixel_size", c_float),
        ("flags", c_uint32),
        ("color", float4),
        ("utf8", c_char_p),
    ]


class SolarPosition(Structure):
    """ABI 1.6. 32 bytes."""

    STRUCT_ID = 13
    ABI_MINOR = 6
    _fields_ = [
        ("to_sun", float3),
        ("elevation_degrees", c_float),
        ("azimuth_degrees", c_float),
        ("declination_degrees", c_float),
        ("hour_angle_degrees", c_float),
        ("equation_of_time_minutes", c_float),
    ]


class TexturedMesh(Structure):
    """ABI 1.7."""

    STRUCT_ID = 14
    ABI_MINOR = 7
    _fields_ = [
        ("mesh", Mesh),
        ("uvs", POINTER(c_float)),
        ("texture", c_uint32),
        ("flags", c_uint32),
    ]


class RayHit(Structure):
    """ABI 1.8. 36 bytes."""

    STRUCT_ID = 15
    ABI_MINOR = 8
    _fields_ = [
        ("mesh_id", c_uint32),
        ("triangle", c_int32),
        ("distance", c_float),
        ("point", float3),
        ("normal", float3),
    ]


class Rect(Structure):
    """ABI 1.11. 32 bytes: a filled screen rectangle, pixels from the top left."""

    STRUCT_ID = 16
    ABI_MINOR = 11
    _fields_ = [
        ("x", c_float),
        ("y", c_float),
        ("width", c_float),
        ("height", c_float),
        ("color", float4),
    ]


#: Every boundary struct, in STRATA_STRUCT_* id order.
STRUCTS = (
    RenderItem,
    EngineDesc,
    Camera,
    Lighting,
    FrameDesc,
    FrameStats,
    Event,
    PointLight,
    TerrainTile,
    Mesh,
    LineVertex,
    Point,
    Text,
    SolarPosition,
    TexturedMesh,
    RayHit,
    Rect,
)

# ---------------------------------------------------------------- functions

LogFn = ctypes.CFUNCTYPE(None, c_int32, c_char_p, c_void_p)

_p = POINTER
_engine = c_void_p
_status = c_int32

#: name: (ABI minor, return type, argument types)
FUNCTIONS = {
    "strata_abi_version": (0, c_uint32, []),
    "strata_abi_minor": (1, c_uint32, []),
    "strata_version_string": (0, c_char_p, []),
    "strata_struct_size": (0, c_int32, [c_int32]),
    "strata_struct_field_offset": (0, c_int32, [c_int32, c_int32]),
    "strata_last_error": (0, c_char_p, []),
    "strata_status_string": (0, c_char_p, [_status]),
    "strata_set_log_callback": (0, None, [LogFn, c_void_p]),
    "strata_create": (0, _status, [_p(EngineDesc), _p(c_void_p)]),
    "strata_destroy": (0, None, [_engine]),
    "strata_gpu_description": (0, c_char_p, [_engine]),
    "strata_poll_event": (0, c_int32, [_engine, _p(Event)]),
    "strata_push_event": (12, _status, [_engine, _p(Event)]),
    "strata_frame_seconds": (12, _status, [_engine, _p(c_double)]),
    "strata_is_driven": (12, c_int32, [_engine]),
    "strata_get_drawable_size": (0, _status, [_engine, _p(c_int32), _p(c_int32)]),
    "strata_resize": (0, _status, [_engine, c_int32, c_int32]),
    "strata_set_camera": (0, _status, [_engine, _p(Camera)]),
    "strata_camera_look_at": (
        0,
        _status,
        [_p(c_float), _p(c_float), _p(c_float), c_float, c_float, c_float, c_float, _p(Camera)],
    ),
    "strata_lighting_defaults": (0, _status, [c_int32, _p(Lighting)]),
    "strata_set_lighting": (0, _status, [_engine, _p(Lighting)]),
    "strata_solar_position": (6, _status, [c_double, c_double, c_double, _p(SolarPosition)]),
    "strata_set_sun_direction": (6, _status, [_engine, _p(c_float)]),
    "strata_set_point_lights": (1, _status, [_engine, _p(PointLight), c_int32]),
    "strata_set_terrain": (3, _status, [_engine, _p(TerrainTile)]),
    "strata_add_mesh": (4, _status, [_engine, _p(Mesh), _p(c_uint32)]),
    "strata_remove_mesh": (4, _status, [_engine, c_uint32]),
    "strata_clear_meshes": (4, _status, [_engine]),
    "strata_create_texture_rgba8": (
        7,
        _status,
        [_engine, c_int32, c_int32, _p(c_uint8), c_uint32, _p(c_uint32)],
    ),
    "strata_update_texture_rgba8": (
        8,
        _status,
        [_engine, c_uint32, c_int32, c_int32, c_int32, c_int32, _p(c_uint8)],
    ),
    "strata_destroy_texture": (7, _status, [_engine, c_uint32]),
    "strata_add_textured_mesh": (7, _status, [_engine, _p(TexturedMesh), _p(c_uint32)]),
    "strata_set_mesh_transform": (7, _status, [_engine, c_uint32, _p(c_float)]),
    "strata_raycast_meshes": (8, _status, [_engine, _p(c_float), _p(c_float), c_float, _p(RayHit)]),
    "strata_set_relative_mouse_mode": (8, _status, [_engine, c_int32]),
    "strata_begin_frame": (0, _status, [_engine, _p(FrameDesc)]),
    "strata_submit_primitives": (0, _status, [_engine, _p(RenderItem), c_int32]),
    "strata_submit_lines": (5, _status, [_engine, _p(LineVertex), c_int32]),
    "strata_submit_points": (5, _status, [_engine, _p(Point), c_int32]),
    "strata_submit_text": (5, _status, [_engine, _p(Text), c_int32]),
    "strata_submit_lines_ex": (11, _status, [_engine, _p(LineVertex), c_int32, c_uint32]),
    "strata_submit_rects": (11, _status, [_engine, _p(Rect), c_int32]),
    "strata_end_frame": (0, _status, [_engine]),
    "strata_present": (0, _status, [_engine]),
    "strata_get_frame_stats": (0, _status, [_engine, _p(FrameStats)]),
    "strata_read_pixels_rgba8": (0, _status, [_engine, _p(c_uint8), c_int32, _p(c_int32), _p(c_int32)]),
    "strata_read_depth_f32": (9, _status, [_engine, _p(c_float), c_int32, _p(c_int32), _p(c_int32)]),
    "strata_save_screenshot_png": (0, _status, [_engine, c_char_p]),
}


class StrataError(RuntimeError):
    """A Strata call failed. status is the StrataStatus (0 never appears)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class StrataAbiError(StrataError):
    """The loaded library does not match this binding's declarations."""

    def __init__(self, message: str):
        super().__init__(ERROR_ABI_MISMATCH, message)


def bind(lib: ctypes.CDLL) -> dict:
    """Sets argtypes and restype on every function the library exports.

    Returns {name: minor} for the declared functions the library does not
    export (older libraries); calling those raises StrataError.
    """
    missing = {}
    for name, (minor, restype, argtypes) in FUNCTIONS.items():
        try:
            fn = getattr(lib, name)
        except AttributeError:
            missing[name] = minor
            continue
        fn.restype = restype
        fn.argtypes = argtypes
    return missing


def check_layouts(lib: ctypes.CDLL, library_minor: int) -> int:
    """Compares every struct the library has with its ctypes mirror.

    Structs newer than library_minor are skipped. Raises StrataAbiError on
    the first size or offset that disagrees. Returns how many were checked.
    """
    checked = 0
    for cls in STRUCTS:
        if cls.ABI_MINOR > library_minor:
            continue
        native_size = lib.strata_struct_size(cls.STRUCT_ID)
        if native_size != ctypes.sizeof(cls):
            raise StrataAbiError(
                "%s is %d bytes in this binding but %d in the library" % (cls.__name__, ctypes.sizeof(cls), native_size)
            )
        for field_id, (name, _type) in enumerate(cls._fields_):
            native = lib.strata_struct_field_offset(cls.STRUCT_ID, field_id)
            mine = getattr(cls, name).offset
            if native != mine:
                raise StrataAbiError(
                    "%s.%s is at offset %d in this binding but %d in the library" % (cls.__name__, name, mine, native)
                )
        extra = lib.strata_struct_field_offset(cls.STRUCT_ID, len(cls._fields_))
        if extra != -1:
            raise StrataAbiError("%s has more fields in the library than in this binding" % cls.__name__)
        checked += 1
    return checked


#: What `from strata import *` exports: constants, structs, errors and callback types.
__all__ = [
    _name
    for _name, _value in list(globals().items())
    if not _name.startswith("_")
    and (
        (_name.isupper() and isinstance(_value, (int, tuple, dict)))
        or (isinstance(_value, type) and _value is not Structure and issubclass(_value, (Structure, Exception)))
    )
] + ["GetProcAddressFn", "LogFn", "bind", "check_layouts"]
