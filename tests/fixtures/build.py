"""Generate the binary fixtures (``.usdc`` / ``.usdz``) from committed ``.usda`` sources.

Run directly (``python tests/fixtures/build.py``) or let ``tests/conftest.py`` run
it before collection. Outputs are git-ignored, so the repository holds only text
sources and the binaries are reproducible from them.
"""

from __future__ import annotations

import pathlib
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
PASS = HERE / "pass"
FAIL = HERE / "fail"

#: Paths (relative to this directory) of every file this script writes.
OUTPUTS = (
    "pass/crate.usdc",
    "pass/crate.usdz",
    "fail/usdz_compressed/asset.usdz",
    "fail/usdz_misaligned/asset.usdz",
)

_ENTRY = """#usda 1.0
(
    defaultPrim = "Crate"
    metersPerUnit = 1
    upAxis = "Y"
)

def Xform "Crate" (
    kind = "component"
)
{
}
"""

_FIXED_DATE = (2026, 1, 1, 0, 0, 0)


def _zip_single(path: pathlib.Path, compression: int) -> None:
    """A one-entry zip with a fixed timestamp, so the bytes are reproducible."""
    path.parent.mkdir(parents=True, exist_ok=True)
    info = zipfile.ZipInfo("asset.usda", date_time=_FIXED_DATE)
    info.compress_type = compression
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(info, _ENTRY)


def build() -> list[pathlib.Path]:
    from pxr import Sdf, UsdUtils

    written = []

    usdc = PASS / "crate.usdc"
    layer = Sdf.Layer.FindOrOpen(str(PASS / "crate.usda"))
    if not layer.Export(str(usdc)):
        raise RuntimeError(f"could not export {usdc}")
    written.append(usdc)

    usdz = PASS / "crate.usdz"
    if usdz.exists():
        usdz.unlink()
    if not UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(PASS / "crate.usda")), str(usdz)):
        raise RuntimeError(f"could not package {usdz}")
    written.append(usdz)

    # Deflate-compressed entries: OpenUSD's usdz resolver refuses to open these.
    compressed = FAIL / "usdz_compressed" / "asset.usdz"
    _zip_single(compressed, zipfile.ZIP_DEFLATED)
    written.append(compressed)

    # Stored but not 64-byte aligned: the entry data starts at offset 30 + len(name).
    misaligned = FAIL / "usdz_misaligned" / "asset.usdz"
    _zip_single(misaligned, zipfile.ZIP_STORED)
    written.append(misaligned)

    return written


if __name__ == "__main__":
    for p in build():
        print(p.relative_to(HERE.parent.parent))
