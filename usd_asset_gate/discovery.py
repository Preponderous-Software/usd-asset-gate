"""Turn command-line paths into the list of USD files to check."""

from __future__ import annotations

import os

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
