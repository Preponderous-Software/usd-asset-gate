"""Image comparison with Strata's golden-image policy, ported to numpy, and PNG output.

A pixel differs when its CIE76 colour difference (sRGB → CIE L*a*b*, D65) exceeds
``threshold`` (4.0); two images show "no visible change" when no more than
``max_fraction`` (0.5 %) of pixels differ. The arithmetic follows Strata's
``src/image/compare.cpp`` line for line, including the diff picture: the "before" image
dimmed to grey, with every differing pixel painted red (brighter = larger ΔE).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import asdict, dataclass

import numpy as np

DEFAULT_THRESHOLD = 4.0
DEFAULT_MAX_FRACTION = 0.005


@dataclass
class CompareResult:
    pixels: int
    pixels_over: int
    fraction_over: float
    max_delta_e: float
    mean_delta_e: float
    visible_change: bool

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("fraction_over", "max_delta_e", "mean_delta_e"):
            d[k] = round(d[k], 6)
        return d


def _lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, np.power((c + 0.055) / 1.055, 2.4))
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / 1.00000
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883
    delta = 6.0 / 29.0

    def f(t):
        return np.where(t > delta**3, np.cbrt(t), t / (3 * delta * delta) + 4.0 / 29.0)

    fx, fy, fz = f(x), f(y), f(z)
    return np.stack([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], axis=-1)


def delta_e(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Per-pixel CIE76 ΔE between two (H, W, 3) uint8 sRGB images."""
    d = np.sqrt(((_lab(a) - _lab(b)) ** 2).sum(axis=-1))
    d[(a == b).all(axis=-1)] = 0.0
    return d


def compare(
    before: np.ndarray,
    after: np.ndarray,
    threshold: float = DEFAULT_THRESHOLD,
    max_fraction: float = DEFAULT_MAX_FRACTION,
) -> tuple[CompareResult, np.ndarray]:
    """Compare two same-size images; returns the statistics and the diff picture."""
    if before.shape != after.shape:
        raise ValueError(f"image sizes differ: {before.shape} vs {after.shape}")
    d = delta_e(before, after)
    over = d > threshold
    n = int(d.size)
    pixels_over = int(over.sum())
    fraction = pixels_over / n if n else 1.0
    result = CompareResult(
        pixels=n,
        pixels_over=pixels_over,
        fraction_over=fraction,
        max_delta_e=float(d.max()) if n else 0.0,
        mean_delta_e=float(d.mean()) if n else 0.0,
        visible_change=fraction > max_fraction,
    )
    e = before.astype(np.float64)
    grey = ((0.299 * e[..., 0] + 0.587 * e[..., 1] + 0.114 * e[..., 2]) * 0.35).astype(np.uint8)
    diff = np.repeat(grey[..., None], 3, axis=-1)
    diff[over] = 0
    diff[..., 0][over] = np.minimum(255.0, 128.0 + d[over] * 4.0).astype(np.uint8)
    return result, diff


def strip(images: list[np.ndarray], gap: int = 8, background=(255, 255, 255)) -> np.ndarray:
    """Place same-height images side by side with a gap: before | after | diff."""
    h = max(i.shape[0] for i in images)
    w = sum(i.shape[1] for i in images) + gap * (len(images) - 1)
    out = np.empty((h, w, 3), dtype=np.uint8)
    out[:] = background
    x = 0
    for img in images:
        out[: img.shape[0], x : x + img.shape[1]] = img
        x += img.shape[1] + gap
    return out


def write_png(path: str, rgb: np.ndarray) -> None:
    """Write an (H, W, 3) uint8 image as an 8-bit RGB PNG (standard library only)."""
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)

