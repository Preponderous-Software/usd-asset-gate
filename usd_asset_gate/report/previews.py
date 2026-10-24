"""The "Visual changes" Markdown section, from a ``preview --json`` document.

Standard library only: ``comment --previews`` and ``preview --summary`` use it, and the
comment step must not need numpy or Strata.
"""

from __future__ import annotations

import json

HEADING = "### Visual changes (advisory)"


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or "files" not in doc:
        raise ValueError("not a usd-asset-gate previews document (no 'files')")
    return doc


def _fmt_views(entry: dict) -> tuple[str, str]:
    over = [v for v in entry.get("views", []) if "pixels_over" in v]
    if not over:
        return "—", "—"
    pct = max(v["fraction_over"] for v in over) * 100
    return f"{pct:.2f} %", f"{max(v['max_delta_e'] for v in over):.1f}"


def render(doc: dict, run_url: str | None = None) -> str:
    """Markdown for the comment and job summary. Never claims more than was drawn."""
    lines = [HEADING, ""]
    if doc.get("status") == "unavailable":
        lines.append(f"Preview unavailable: {doc.get('message', 'Strata could not start')}.")
        return "\n".join(lines) + "\n"
    files = doc.get("files", [])
    if not files:
        lines.append("No changed USD files to preview.")
        return "\n".join(lines) + "\n"
    s = doc.get("settings", {})
    shown = [f for f in files if f.get("status") != "rendered" or f.get("visible_change", True)]
    quiet = [f for f in files if f not in shown]
    if shown:
        lines += [
            "| File | Change | Pixels over ΔE {:g} (worst view) | Max ΔE | Preview |".format(s.get("threshold", 4.0)),
            "|---|---|---|---|---|",
        ]
        for f in shown:
            pct, max_de = _fmt_views(f)
            if f.get("status") != "rendered":
                note = f"{f.get('status', 'error').replace('_', ' ')}: {f.get('message', '')}".rstrip(": ")
            else:
                note = ", ".join(v["name"].replace("_", " ") for v in f.get("views", [])) or "—"
            lines.append(f"| `{f['path']}` | {f.get('change', '')} | {pct} | {max_de} | {note} |")
        lines.append("")
    if quiet:
        names = ", ".join(f"`{f['path']}`" for f in quiet)
        lines += [f"No visible change in {len(quiet)} file{'s' if len(quiet) != 1 else ''}: {names}.", ""]
    where = f"the [workflow run]({run_url})" if run_url else "the workflow run"
    renderer = doc.get("renderer") or {}
    lines.append(
        f"Before | after | diff images are in {where}. Rendered headless by Strata "
        f"{renderer.get('version', '')} with flat display colours; not drawn: {doc.get('not_drawn', '')}. "
        "This section never fails the check."
    )
    return "\n".join(lines) + "\n"
