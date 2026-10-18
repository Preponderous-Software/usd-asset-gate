"""Regenerate the README images from a real run of the gate on the test fixtures.

    python tests/fixtures/build.py
    python docs/capture_readme.py docs/images [chromium-executable]

* ``docs/images/cli.png``: the text report of ``usd-asset-gate check`` on four fixtures, drawn as
  a terminal (Pillow, DejaVu Sans Mono; long lines are wrapped).
* ``docs/images/summary.png``: the Markdown the same run appends with ``--summary`` (the job
  summary, and the body of the PR comment), rendered by GitHub's own Markdown API
  (``gh api markdown``) and screenshotted in headless Chromium (Playwright).

Needs this package installed, ``gh`` (authenticated), Pillow and Playwright.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

FILES = [
    "tests/fixtures/pass/crate.usda",
    "tests/fixtures/fail/missing_reference",
    "tests/fixtures/fail/no_default_prim",
    "tests/fixtures/fail/no_up_axis",
]
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
BG, FG, DIM, RED, GREEN = (13, 17, 23), (201, 209, 217), (139, 148, 158), (255, 123, 114), (126, 231, 135)


def terminal(command: str, output: str, path: Path, cols: int = 104) -> None:
    font = ImageFont.truetype(FONT, 15)
    lines = [(w, DIM) for w in textwrap.wrap("$ " + command, cols, subsequent_indent="    ")]
    for raw in output.rstrip("\n").split("\n"):
        colour = RED if raw.startswith("FAIL") else FG
        wrapped = textwrap.wrap(raw, cols, subsequent_indent="    ") or [""]
        lines += [(w, colour) for w in wrapped]
    lh, cw = 21, 9
    img = Image.new("RGB", (32 + cw * (cols + 4), 24 + lh * len(lines) + 20), BG)
    d = ImageDraw.Draw(img)
    for i, (text, colour) in enumerate(lines):
        y = 20 + lh * i
        if " error " in text:  # colour the severity word only
            head, tail = text.split(" error ", 1)
            d.text((16, y), head + " ", font=font, fill=colour)
            x = 16 + font.getlength(head + " ")
            d.text((x, y), "error", font=font, fill=RED)
            d.text((x + font.getlength("error "), y), tail, font=font, fill=colour)
        else:
            d.text((16, y), text, font=font, fill=colour)
    img.save(path)


def summary(markdown: str, path: Path, executable: str | None) -> None:
    from playwright.sync_api import sync_playwright

    html = subprocess.run(["gh", "api", "markdown", "--input", "-"], input=json.dumps({"text": markdown, "mode": "gfm"}),
                          capture_output=True, text=True, check=True).stdout
    page_html = f"""<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/github-markdown-css@5.8.1/github-markdown-light.css">
<body style="margin:0;background:#fff"><article class="markdown-body" style="padding:24px 28px;max-width:1500px">{html}</article></body>"""
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        page_file = Path(tmp) / "summary.html"
        page_file.write_text(page_html)
        browser = p.chromium.launch(args=["--no-sandbox"], executable_path=executable)
        page = browser.new_page(viewport={"width": 1560, "height": 400}, device_scale_factor=1)
        page.goto(page_file.as_uri(), wait_until="networkidle")
        page.locator("article").screenshot(path=str(path))
        browser.close()
    # a thin grey frame, as GitHub draws around a comment, so the white page stays visible
    ImageOps.expand(Image.open(path), border=1, fill=(208, 215, 222)).save(path)


def main(out: Path, executable: str | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        md = Path(tmp) / "summary.md"
        args = ["usd-asset-gate", "check", "--config", "tests/fixtures/default-config.yml", *FILES]
        run = subprocess.run([*args, "--summary", str(md)], capture_output=True, text=True)
        assert run.returncode == 1, run  # the fail fixtures must fail
        terminal(" ".join(args), run.stdout, out / "cli.png")
        summary(md.read_text(), out / "summary.png", executable)


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None)
