"""``file-size``: a ceiling on the size of each checked file, per extension.

Runs from ``os.stat`` before the stage is opened, so an oversize file fails fast
and is never loaded.
"""

from __future__ import annotations

import os
import re

from ...model import Finding, Severity, severity_of

#: SI units are powers of 1000, IEC units powers of 1024.
UNITS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
}
SIZE_PATTERN = r"^\s*\d+(\.\d+)?\s*([KMG]i?)?B\s*$"
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMG]i?B|B)\s*$", re.I)


def parse_size(value) -> int:
    """``1048576``, ``"50MB"``, ``"1.5 GiB"`` -> bytes."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    m = _SIZE_RE.match(str(value))
    if not m:
        raise ValueError(f"not a size: {value!r} (use bytes or e.g. 50MB, 2GiB)")
    return int(float(m.group(1)) * UNITS[m.group(2).upper()])


def human(n: int) -> str:
    for unit, size in (("GB", 1000**3), ("MB", 1000**2), ("KB", 1000)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B"


def limit_for(path: str, max_bytes) -> int | None:
    if max_bytes is None:
        return None
    if not isinstance(max_bytes, dict):
        return parse_size(max_bytes)
    ext = os.path.splitext(path)[1].lower()
    value = max_bytes.get(ext, max_bytes.get("default"))
    return None if value is None else parse_size(value)


def check_before_open(ctx, settings: dict) -> list[Finding]:
    limit = limit_for(ctx.path, settings.get("max_bytes"))
    if limit is None:
        return []
    size = os.stat(ctx.abs_path).st_size
    if size <= limit:
        return []
    return [
        Finding(
            rule="file-size",
            code="FileTooLarge",
            severity=severity_of(settings, Severity.ERROR),
            file=ctx.path,
            message=f"file is {human(size)} ({size} bytes); the limit is {human(limit)} "
            f"({limit} bytes). The stage was not opened.",
        )
    ]
