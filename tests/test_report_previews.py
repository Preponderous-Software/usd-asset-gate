"""The "Visual changes" section: loading a previews document and rendering it.

Standard library only, like the module under test; tests/test_preview.py covers the section
as part of the numpy-backed pipeline.
"""

from __future__ import annotations

import json
import re

import pytest

from usd_asset_gate.report import previews

FOOTER = (
    "Before | after | diff images are in the workflow run. Rendered headless by Strata 0.6.0 "
    "with flat display colours; not drawn: opacity. This section never fails the check."
)


def _doc(files, **extra):
    doc = {
        "version": 1,
        "status": "ok",
        "settings": {"threshold": 4.0},
        "renderer": {"version": "0.6.0"},
        "not_drawn": "opacity",
        "files": files,
    }
    doc.update(extra)
    return doc


def _rendered(path, views, visible=True, change="modified"):
    return {"path": path, "change": change, "status": "rendered", "visible_change": visible, "views": views}


# --- load -----------------------------------------------------------------------------


def test_load_returns_the_document(tmp_path):
    path = tmp_path / "previews.json"
    doc = _doc([])
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert previews.load(str(path)) == doc


@pytest.mark.parametrize("content", [[], {"version": 1}, "files"])
def test_load_rejects_a_document_without_files(tmp_path, content):
    path = tmp_path / "previews.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError, match="no 'files'"):
        previews.load(str(path))


@pytest.mark.parametrize(
    "doc, message",
    [
        (_doc("a.usda"), "'files' is a JSON string, expected an array"),
        (_doc({}), "'files' is a JSON object, expected an array"),
        (_doc(None), "'files' is a JSON null, expected an array"),
        (_doc(["a.usda"]), "malformed previews document: "),
        (_doc([{"change": "added"}]), "missing field 'path'"),
        (_doc([_rendered("a.usda", [{"pixels_over": 1}])]), "missing field 'fraction_over'"),
        (_doc([_rendered("a.usda", [{"pixels_over": 1, "fraction_over": 0.1}])]), "missing field 'max_delta_e'"),
        (_doc([_rendered("a.usda", [{"size": 1}])]), "missing field 'name'"),
        (_doc([_rendered("a.usda", ["front"])]), "malformed previews document: "),
        (_doc([_rendered("a.usda", [])], settings={"threshold": "x"}), "malformed previews document: "),
        (_doc([_rendered("a.usda", [])], settings=[]), "malformed previews document: "),
        (_doc([_rendered("a.usda", [])], renderer="0.6.0"), "malformed previews document: "),
        (_doc([{"path": "a.usda", "status": None}]), "malformed previews document: "),
    ],
    ids=[
        "files-string",
        "files-object",
        "files-null",
        "entry-string",
        "entry-no-path",
        "view-no-fraction",
        "view-no-max-delta-e",
        "view-no-name",
        "view-string",
        "threshold-string",
        "settings-array",
        "renderer-string",
        "status-null",
    ],
)
def test_load_rejects_a_document_render_cannot_draw(tmp_path, doc, message):
    path = tmp_path / "previews.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match=re.escape(message)):
        previews.load(str(path))


@pytest.mark.parametrize(
    "doc",
    [
        _doc([_rendered("a.usda", [{"name": "top", "pixels_over": 2, "fraction_over": 0.5, "max_delta_e": 9.0}])]),
        _doc([{"path": "b.usda", "change": "added", "message": "boom"}], settings={}, renderer=None),
        {"status": "unavailable", "message": "no libstrata.so", "files": []},
    ],
    ids=["rendered", "error-entry", "unavailable"],
)
def test_load_accepts_documents_render_can_draw(tmp_path, doc):
    path = tmp_path / "previews.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert previews.load(str(path)) == doc


# --- render ---------------------------------------------------------------------------


def test_full_section_layout():
    doc = _doc([_rendered("a.usda", [{"name": "three_quarter"}], change="added")])
    assert previews.render(doc) == "\n".join(
        [
            previews.HEADING,
            "",
            "| File | Change | Pixels over ΔE 4 (worst view) | Max ΔE | Preview |",
            "|---|---|---|---|---|",
            "| `a.usda` | added | — | — | three quarter |",
            "",
            FOOTER,
        ]
    ) + "\n"


def test_unavailable_wins_and_has_a_default_message():
    doc = _doc([_rendered("a.usda", [])], status="unavailable")
    assert previews.render(doc) == f"{previews.HEADING}\n\nPreview unavailable: Strata could not start.\n"


def test_no_files_says_so():
    assert previews.render(_doc([])) == f"{previews.HEADING}\n\nNo changed USD files to preview.\n"


def test_worst_view_is_reported_across_views():
    views = [
        {"name": "top", "pixels_over": 1, "fraction_over": 0.1, "max_delta_e": 25.0},
        {"name": "three_quarter", "pixels_over": 3, "fraction_over": 0.3333, "max_delta_e": 12.34},
        {"name": "front"},  # drawn on one side only: not compared, so not in the numbers
    ]
    md = previews.render(_doc([_rendered("a.usda", views)]))
    assert "| `a.usda` | modified | 33.33 % | 25.0 | top, three quarter, front |" in md


def test_threshold_heading_uses_the_configured_value_and_defaults_to_4():
    assert "Pixels over ΔE 2.5 (worst view)" in previews.render(
        _doc([_rendered("a.usda", [])], settings={"threshold": 2.5})
    )
    assert "Pixels over ΔE 4 (worst view)" in previews.render(_doc([_rendered("a.usda", [])], settings={}))


def test_non_rendered_notes():
    files = [
        {"path": "a.usda", "change": "modified", "status": "no_geometry", "views": []},
        {"path": "b.usda", "change": "added", "message": "boom"},
        {"path": "c.usda", "change": "modified", "status": "rendered"},
    ]
    md = previews.render(_doc(files))
    assert "| `a.usda` | modified | — | — | no geometry |" in md
    assert "| `b.usda` | added | — | — | error: boom |" in md
    # a rendered file that does not say whether it changed is shown, not collapsed
    assert "| `c.usda` | modified | — | — | — |" in md
    assert "No visible change" not in md


def test_only_quiet_files_collapse_without_a_table():
    md = previews.render(_doc([_rendered("b.usda", [], visible=False), _rendered("c.usda", [], visible=False)]))
    assert "| File |" not in md
    assert "No visible change in 2 files: `b.usda`, `c.usda`." in md
    assert md.endswith(FOOTER + "\n")


def test_footer_links_the_run_when_given():
    md = previews.render(_doc([_rendered("a.usda", [])]), "https://x/run")
    assert "diff images are in the [workflow run](https://x/run)." in md
