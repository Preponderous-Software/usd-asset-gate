"""Finding, fetching and loading libstrata.

The binding never bundles the native library. It loads one of:

1. a path passed to load(path=...);
2. the file named by the STRATA_LIBRARY environment variable;
3. libstrata.so / strata.dll in the directory named by STRATA_NATIVE_DIR
   (the variable the C# tests use), such as a local build directory;
4. a released library fetched by fetch_release(version, sha256), which
   downloads the bare library asset of a Strata GitHub release into a per-user
   cache and checks it against a SHA-256 the caller pins (copied from that
   release's SHA256SUMS). A file whose hash does not match is deleted and never
   loaded.

Desktop Linux (x86-64) and Windows (x64) only: those are the platforms with
released libraries. The browser build is a static WebAssembly archive for
C and C++ hosts; Python in the browser (Pyodide) is out of scope.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import platform as _platform
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

RELEASES_URL = "https://github.com/Preponderous-Software/strata/releases/download"

#: SHA-256 of the bare library assets of published releases, from each
#: release's SHA256SUMS. fetch_release uses these when no hash is passed.
KNOWN_RELEASES = {
    ("0.4.0", "linux-x64"): "6044127f21c33abbd03d4e39182b5a6082c73e92479236bbd0aea6e68594b181",
    ("0.4.0", "win-x64"): "1d34a949f74e47055d019e0fc7b646a14d81ce2645f6e0642a7d1b78110fdfe7",
}


class StrataLoadError(RuntimeError):
    """No library could be found, fetched, verified or loaded."""


def platform_tag() -> str:
    """The release platform name for this machine: linux-x64 or win-x64."""
    machine = _platform.machine().lower()
    if sys.platform.startswith("linux") and machine in ("x86_64", "amd64"):
        return "linux-x64"
    if sys.platform == "win32" and machine in ("amd64", "x86_64"):
        return "win-x64"
    raise StrataLoadError(
        "Strata publishes libraries for Linux x86-64 and Windows x64 only (this is %s on %s); "
        "build libstrata from source and set STRATA_LIBRARY" % (sys.platform, machine or "unknown")
    )


def library_file_name() -> str:
    return "strata.dll" if sys.platform == "win32" else "libstrata.so"


def asset_name(version: str, platform: str) -> str:
    if platform == "win-x64":
        return "strata-%s-win-x64.dll" % version
    return "libstrata-%s-%s.so" % (version, platform)


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (
        os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
    )
    return Path(base) / "strata" if base else Path.home() / ".cache" / "strata"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_release(
    version: str,
    sha256: Optional[str] = None,
    cache_dir: Optional[os.PathLike] = None,
    platform: Optional[str] = None,
) -> Path:
    """Downloads a released library (once) and returns its verified path.

    version is a release such as "0.4.0". sha256 is the asset's hash from that
    release's SHA256SUMS; it may be left out only for releases listed in
    KNOWN_RELEASES. A cached copy is re-hashed on every call. On a mismatch
    the file is deleted and StrataLoadError is raised.
    """
    platform = platform or platform_tag()
    expected = (sha256 or KNOWN_RELEASES.get((version, platform)) or "").lower()
    if len(expected) != 64:
        raise StrataLoadError(
            "pin the SHA-256 of %s from the v%s release's SHA256SUMS" % (asset_name(version, platform), version)
        )
    directory = Path(cache_dir) if cache_dir else default_cache_dir() / version
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / asset_name(version, platform)
    if target.exists():
        if _sha256(target) == expected:
            return target
        target.unlink()
    url = "%s/v%s/%s" % (RELEASES_URL, version, asset_name(version, platform))
    fd, partial = tempfile.mkstemp(dir=str(directory), suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(url, timeout=120) as response:
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                out.write(block)
        actual = _sha256(Path(partial))
        if actual != expected:
            raise StrataLoadError("%s has SHA-256 %s, but %s was pinned; it was deleted" % (url, actual, expected))
        os.replace(partial, target)
    finally:
        if os.path.exists(partial):
            os.remove(partial)
    return target


def resolve_library_path(path: Optional[os.PathLike] = None) -> Path:
    """The library load() would open, without opening it."""
    if path:
        return Path(path)
    explicit = os.environ.get("STRATA_LIBRARY")
    if explicit:
        return Path(explicit)
    directory = os.environ.get("STRATA_NATIVE_DIR")
    if directory:
        return Path(directory) / library_file_name()
    raise StrataLoadError(
        "no Strata library: pass a path, set STRATA_LIBRARY (a file) or STRATA_NATIVE_DIR (a directory), "
        "or use strata.load_release(version, sha256)"
    )


def open_library(path: Path) -> ctypes.CDLL:
    if not path.is_file():
        raise StrataLoadError("%s does not exist" % path)
    try:
        return ctypes.CDLL(str(path.resolve()))
    except OSError as e:
        raise StrataLoadError("could not load %s: %s" % (path, e)) from e
