"""The ``preview`` pipeline: changed files → before/after stages → cameras → Strata → ΔE.

The "before" side of a changed file is composed from a temporary, detached
``git worktree`` at the merge base of ``--changed-since`` and ``HEAD``, so its references
and sublayers resolve as they did there. A file new in the change has only "after", a
deleted one only "before"; neither gets a diff. The run never fails on what the images
show: it writes ``previews.json`` and the PNGs and returns.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field

import numpy as np

from . import compare as cmp
from .flatten import DEFAULT_MAX_TRIANGLES, FlatStage, TooLarge, flatten_file

VIEW_DIRECTIONS = {
    # unit-ish vectors from the target towards the eye, in Strata's Y-up frame
    "three_quarter": (0.6, 0.45, 0.66),
    "three_quarter_back": (-0.6, 0.45, -0.66),
    "top": (0.0, 1.0, 0.001),
    "front": (0.0, 0.0, 1.0),
}
FOV_Y_DEGREES = 40.0
#: What a Strata preview does not draw (RFC 0028 §5); repeated on every report.
NOT_DRAWN = (
    "subdivision (the control cage is drawn), basis curves, points, volumes, skinning, "
    "textures and every material input except a constant diffuseColor, opacity, scene lights"
)


@dataclass
class Settings:
    views: list[str] = field(default_factory=lambda: ["three_quarter", "three_quarter_back"])
    size: tuple[int, int] = (640, 360)
    max_triangles: int = DEFAULT_MAX_TRIANGLES
    threshold: float = cmp.DEFAULT_THRESHOLD
    max_fraction: float = cmp.DEFAULT_MAX_FRACTION

    @classmethod
    def from_config(cls, data: dict | None) -> "Settings":
        data = data or {}
        s = cls()
        if "views" in data:
            s.views = list(data["views"])
        if "size" in data:
            s.size = (int(data["size"][0]), int(data["size"][1]))
        for key in ("max_triangles", "threshold", "max_fraction"):
            if key in data:
                setattr(s, key, type(getattr(s, key))(data[key]))
        return s

    def to_dict(self) -> dict:
        return {
            "views": self.views,
            "size": list(self.size),
            "max_triangles": self.max_triangles,
            "threshold": self.threshold,
            "max_fraction": self.max_fraction,
        }


# --- cameras -----------------------------------------------------------------------------


def frame_bounds(stages: list[FlatStage | None]):
    """Centre and radius of the union of the stages' bounds (None when nothing is drawn)."""
    boxes = [s.bounds() for s in stages if s is not None and s.bounds() is not None]
    if not boxes:
        return None
    lo = np.min([b[0] for b in boxes], axis=0).astype(np.float64)
    hi = np.max([b[1] for b in boxes], axis=0).astype(np.float64)
    return (lo + hi) / 2.0, max(float(np.linalg.norm(hi - lo)) / 2.0, 1e-6)


def camera_for(view: str, centre, radius: float, aspect: float):
    """Eye, target, near and far that fit the bounding sphere in the view."""
    half_v = math.radians(FOV_Y_DEGREES / 2)
    half_h = math.atan(math.tan(half_v) * aspect)
    dist = radius / math.sin(min(half_v, half_h)) * 1.05
    d = np.asarray(VIEW_DIRECTIONS[view], dtype=np.float64)
    eye = centre + d / np.linalg.norm(d) * dist
    return eye, centre, max(dist - radius * 1.5, dist * 0.01), dist + radius * 1.5


# --- git ---------------------------------------------------------------------------------


@dataclass
class Change:
    path: str  # relative to the repository top, forward slashes
    kind: str  # added | modified | deleted


def changes_since(ref: str, top: str) -> list[Change]:
    """USD files added, modified or deleted on this branch since it left ``ref``.

    Renames count as a deletion plus an addition (``--no-renames``).
    """
    from ..discovery import _git, is_usd_file

    out = _git(["diff", "--name-status", "-z", "--no-renames", "--diff-filter=ACMD", f"{ref}...HEAD"], cwd=top)
    parts = [os.fsdecode(p) for p in out.split(b"\0") if p]
    kinds = {"A": "added", "C": "added", "M": "modified", "D": "deleted"}
    changes = []
    for status, name in zip(parts[0::2], parts[1::2]):
        if is_usd_file(name):
            changes.append(Change(name.replace(os.sep, "/"), kinds[status[0]]))
    return sorted(changes, key=lambda c: c.path)


class BaseWorktree:
    """A temporary detached worktree at the merge base of ``ref`` and HEAD."""

    def __init__(self, ref: str, top: str):
        self.ref, self.top, self.path = ref, top, None

    def __enter__(self) -> str:
        from ..discovery import DiscoveryError, _git

        base = os.fsdecode(_git(["merge-base", self.ref, "HEAD"], cwd=self.top)).strip()
        self.path = tempfile.mkdtemp(prefix="usd-asset-gate-base-")
        os.rmdir(self.path)
        try:
            _git(["worktree", "add", "--detach", "--quiet", self.path, base], cwd=self.top)
        except DiscoveryError:
            self.path = None
            raise
        return self.path

    def __exit__(self, *exc):
        from ..discovery import DiscoveryError, _git

        if self.path is None:
            return
        try:
            _git(["worktree", "remove", "--force", self.path], cwd=self.top)
        except DiscoveryError:
            shutil.rmtree(self.path, ignore_errors=True)
            _git(["worktree", "prune"], cwd=self.top)


# --- rendering ---------------------------------------------------------------------------


def _slug(path: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in path)


def _flatten(path: str | None, settings: Settings, entry: dict, side: str):
    if path is None:
        return None
    try:
        flat = flatten_file(path, settings.max_triangles)
    except TooLarge as exc:
        entry["status"] = "too_large"
        entry["message"] = f"{side}: too large to preview ({exc})"
        return None
    except Exception as exc:  # an unopenable stage is the check's job to report
        entry["status"] = "error"
        entry["message"] = f"{side}: cannot flatten: {exc}"
        return None
    entry["triangles"][side] = flat.triangle_count
    for t, n in flat.unsupported.items():
        entry["not_drawn"][t] = entry["not_drawn"].get(t, 0) + n
    return flat


def preview_file(renderer, rel: str, kind: str, before_path, after_path, out_dir: str, settings: Settings) -> dict:
    entry: dict = {"path": rel, "change": kind, "status": "rendered", "triangles": {}, "not_drawn": {}, "views": []}
    t0 = time.perf_counter()
    before = _flatten(before_path, settings, entry, "before")
    after = _flatten(after_path, settings, entry, "after") if entry["status"] == "rendered" else None
    if entry["status"] != "rendered":
        return entry
    framing = frame_bounds([before, after])
    if framing is None:
        entry["status"] = "no_geometry"
        entry["message"] = "nothing drawable (no meshes or implicit gprims)"
        return entry
    centre, radius = framing
    w, h = settings.size
    slug = _slug(rel)
    visible = False
    for view in settings.views:
        eye, target, near, far = camera_for(view, centre, radius, w / h)
        row: dict = {"name": view}
        images = {}
        for side, flat in (("before", before), ("after", after)):
            if flat is None:
                continue
            img = renderer.render(flat.batches(), eye, target, FOV_Y_DEGREES, near, far)
            name = f"{slug}.{view}.{side}.png"
            cmp.write_png(os.path.join(out_dir, name), img)
            row[side] = name
            images[side] = img
        if len(images) == 2:
            result, diff = cmp.compare(images["before"], images["after"], settings.threshold, settings.max_fraction)
            row["diff"] = f"{slug}.{view}.diff.png"
            cmp.write_png(os.path.join(out_dir, row["diff"]), diff)
            row["strip"] = f"{slug}.{view}.strip.png"
            cmp.write_png(os.path.join(out_dir, row["strip"]), cmp.strip([images["before"], images["after"], diff]))
            row.update(result.to_dict())
            visible = visible or result.visible_change
        entry["views"].append(row)
    entry["visible_change"] = visible if kind == "modified" else True
    entry["seconds"] = round(time.perf_counter() - t0, 3)
    return entry


def run(
    files: list[tuple[str, str, str | None, str | None]],
    out_dir: str,
    settings: Settings,
    renderer_factory,
) -> dict:
    """Render every (rel, kind, before_path, after_path); return the previews document."""
    os.makedirs(out_dir, exist_ok=True)
    doc: dict = {
        "version": 1,
        "status": "ok",
        "settings": settings.to_dict(),
        "not_drawn": NOT_DRAWN,
        "files": [],
    }
    if not files:
        return doc
    try:
        renderer = renderer_factory(*settings.size)
    except Exception as exc:
        doc["status"] = "unavailable"
        doc["message"] = f"Strata could not start: {exc}"
        return doc
    doc["renderer"] = {"name": "strata", "version": renderer.version, "gpu": renderer.gpu}
    try:
        for rel, kind, before, after in files:
            try:
                entry = preview_file(renderer, rel, kind, before, after, out_dir, settings)
            except Exception as exc:  # one bad asset must not hide the others
                entry = {"path": rel, "change": kind, "status": "error", "message": str(exc), "views": []}
            doc["files"].append(entry)
    finally:
        renderer.close()
    return doc


def write_json(doc: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
