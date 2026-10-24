"""Regenerate docs/images/preview-yard.png from a real ``usd-asset-gate preview`` run.

    STRATA_LIBRARY=/path/to/libstrata.so LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe \\
        python docs/capture_preview.py OUT_DIR [docs/images/preview-yard.png]

Copies ``tests/fixtures/pass`` into a throwaway git repository, commits it, then commits
one change to ``yard.usda``: ``CrateB`` moves 0.8 m along +Z and gets an orange
``displayColor`` on its instance root (inherited by the instanced crate under it). It
runs ``usd-asset-gate preview --changed-since HEAD~1`` there, leaving the PNGs and
``previews.json`` in OUT_DIR. With a second argument, the three-quarter before | after |
diff strip is copied there, with captions when Pillow is installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHANGE = (
    "double3 xformOp:translate = (2, 0, 0)",
    'color3f[] primvars:displayColor = [(0.9, 0.35, 0.05)]\n            double3 xformOp:translate = (2, 0, 0.8)',
)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=demo@example.com", "-c", "user.name=demo", *args], cwd=cwd, check=True)


def caption(strip: Path, dest: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        shutil.copyfile(strip, dest)
        return
    img = Image.open(strip).convert("RGB")
    band = 34
    out = Image.new("RGB", (img.width, img.height + band), "white")
    out.paste(img, (0, band))
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    panel = (img.width - 16) // 3
    for i, text in enumerate(["before (base)", "after (this change)", "diff: pixels over ΔE 4 in red"]):
        draw.text((i * (panel + 8) + 10, 6), text, fill=(40, 40, 40), font=font)
    out.save(dest, optimize=True)


def main(out_dir: str, image: str | None = None) -> int:
    out = Path(out_dir).resolve()
    with tempfile.TemporaryDirectory(prefix="usd-asset-gate-demo-") as tmp:
        work = Path(tmp)
        shutil.copytree(REPO / "tests" / "fixtures" / "pass", work / "assets",
                        ignore=shutil.ignore_patterns("*.usdc", "*.usdz"))
        (work / ".usd-asset-gate.yml").write_text("version: 2\npreview:\n  enabled: true\n")
        git(work, "init", "-q")
        git(work, "add", ".")
        git(work, "commit", "-qm", "base")
        yard = work / "assets" / "yard.usda"
        yard.write_text(yard.read_text().replace(*CHANGE))
        git(work, "commit", "-qam", "Move CrateB and paint it orange")
        subprocess.run(["git", "--no-pager", "diff", "HEAD~1"], cwd=work, check=True)
        code = subprocess.run(
            [sys.executable, "-m", "usd_asset_gate", "preview", "--changed-since", "HEAD~1",
             "--out", str(out), "--json", str(out / "previews.json")]
            + (["--summary", os.environ["GITHUB_STEP_SUMMARY"]] if os.environ.get("GITHUB_STEP_SUMMARY") else []),
            cwd=work,
            env={**os.environ, "PYTHONPATH": str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", "")},
        ).returncode
    if code != 0:
        return code
    if image:
        caption(out / "assets_yard.usda.three_quarter.strip.png", Path(image))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
