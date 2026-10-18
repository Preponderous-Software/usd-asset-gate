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


def _git(args: list[str], cwd: str | None = None) -> str:
    import subprocess

    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        raise DiscoveryError("git is not installed or not on PATH") from None
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit code {proc.returncode}"
        raise DiscoveryError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout


def changed_files(ref: str) -> list[str]:
    """USD files added, copied, modified or renamed between ``ref`` and ``HEAD``.

    Uses ``git diff --name-only --diff-filter=ACMR <ref>...HEAD`` (three dots: the
    changes on this branch since it left ``ref``). Deleted files are never listed.
    Paths are returned relative to the working directory, forward-slashed.
    """
    import subprocess

    try:
        known = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise DiscoveryError("git is not installed or not on PATH") from None
    if known.returncode != 0:
        raise DiscoveryError(
            f"--changed-since: ref {ref!r} is not present in this clone; fetch it first, "
            f"for example: git fetch origin {ref}"
        )
    top = _git(["rev-parse", "--show-toplevel"]).strip()
    out = _git(
        ["diff", "--name-only", "-z", "--diff-filter=ACMR", f"{ref}...HEAD"], cwd=top
    )
    files = []
    for name in out.split("\0"):
        if not name or not is_usd_file(name):
            continue
        full = os.path.join(top, name)
        if os.path.isfile(full):
            files.append(display_path(full))
    return sorted(set(files))


def under_any(path: str, roots: list[str]) -> bool:
    """True when ``path`` is one of ``roots`` or inside one of them (display paths)."""
    for root in roots:
        root = display_path(root).rstrip("/")
        if root in ("", "."):
            return True
        if path == root or path.startswith(root + "/"):
            return True
    return False
