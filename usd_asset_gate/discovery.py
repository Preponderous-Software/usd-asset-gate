"""Turn command-line paths into the list of USD files to check."""

from __future__ import annotations

import os
import re
from typing import Iterable

#: File extensions treated as USD assets (compared case-insensitively).
USD_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")


class DiscoveryError(Exception):
    """A path or ref could not be resolved; the CLI exits 2."""


def is_usd_file(path: str) -> bool:
    return path.lower().endswith(USD_EXTENSIONS)


def display_path(path: str) -> str:
    """Forward-slash path relative to the working directory when it is inside it."""
    abs_path = os.path.abspath(path)
    try:
        rel = os.path.relpath(abs_path)
    except ValueError:  # Windows: different drive or UNC share than the working dir
        rel = abs_path
    if rel == ".." or rel.startswith(".." + os.sep):
        rel = abs_path
    return rel.replace(os.sep, "/")


def _raise_walk_error(err: OSError) -> None:
    raise DiscoveryError(f"cannot read directory {err.filename}: {err.strerror}")


def expand_paths(paths: list[str]) -> list[tuple[str, bool]]:
    """Expand files and directories into ``(display_path, explicit)`` pairs.

    ``explicit`` is True for a file named directly on the command line. Directories
    are walked recursively and contribute only files with a USD extension. Results
    are de-duplicated and sorted.
    """
    found: dict[str, bool] = {}
    for raw in paths:
        if os.path.isdir(raw):
            for root, dirs, files in os.walk(raw, onerror=_raise_walk_error):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for name in files:
                    if is_usd_file(name):
                        found.setdefault(display_path(os.path.join(root, name)), False)
        elif os.path.isfile(raw):
            found[display_path(raw)] = True
        else:
            raise DiscoveryError(f"no such file or directory: {raw}")
    return sorted(found.items())


def glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """Compile a path glob: ``**`` spans directories, ``*`` and ``?`` stay in one segment.

    Patterns match the whole forward-slash path relative to the working directory;
    ``**/`` also matches zero directories, so ``**/*.usda`` matches ``a.usda``.
    """
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches_glob(path: str, patterns: Iterable[str]) -> bool:
    return any(glob_to_regex(p).match(path) for p in patterns)


def filter_paths(
    paths: Iterable[str], include: Iterable[str] | None, exclude: Iterable[str]
) -> list[str]:
    """Keep paths matching any ``include`` glob (all when None) and no ``exclude`` glob."""
    include = list(include) if include is not None else None
    exclude = list(exclude)
    kept = []
    for p in paths:
        if include is not None and not matches_glob(p, include):
            continue
        if matches_glob(p, exclude):
            continue
        kept.append(p)
    return kept
