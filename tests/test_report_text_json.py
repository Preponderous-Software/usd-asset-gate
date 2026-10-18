"""Text and JSON reporters."""

from __future__ import annotations

import json

from usd_asset_gate.model import Finding, Report, Severity
from usd_asset_gate.report import json as json_report
from usd_asset_gate.report import text as text_report

FINDING = Finding(
    rule="default-prim",
    code="MissingDefaultPrim",
    severity=Severity.ERROR,
    file="props/crate.usda",
    message="no default prim",
    prim_path="/Crate",
    line=7,
)


def test_text_line_format():
    assert text_report.format_finding(FINDING) == (
        "props/crate.usda:7:/Crate: error default-prim: no default prim [MissingDefaultPrim]"
    )


def test_text_mentions_layer_and_fixer():
    finding = Finding(
        rule="r",
        code="C",
        severity=Severity.WARNING,
        file="a.usda",
        message="m",
        layer="sub/b.usda",
        fixer_available=True,
    )
    line = text_report.format_finding(finding)
    assert line == "a.usda: warning r: m [C] (in layer sub/b.usda) (an OpenUSD fixer exists; not applied)"


def test_text_colour_only_when_requested():
    assert "\033[" not in text_report.format_finding(FINDING)
    assert "\033[31merror\033[0m" in text_report.format_finding(FINDING, colour=True)


def test_text_render_ends_with_summary():
    report = Report(files=["props/crate.usda", "b.usda"], findings=[FINDING])
    out = text_report.render(report)
    assert out.splitlines()[-1] == (
        "FAIL: 2 files checked, 1 error(s), 0 warning(s), 0 info (fail_on: error)"
    )


def test_use_colour_respects_no_color(monkeypatch):
    class Tty:
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert text_report.use_colour(Tty())
    monkeypatch.setenv("NO_COLOR", "1")
    assert not text_report.use_colour(Tty())


def test_json_round_trip():
    report = Report(files=["props/crate.usda", "b.usda"], findings=[FINDING], fail_on="warning")
    data = json.loads(json_report.render(report))
    assert data["files"] == [
        {"path": "props/crate.usda", "findings": 1},
        {"path": "b.usda", "findings": 0},
    ]
    back = json_report.from_dict(data)
    assert back.files == report.files
    assert back.findings == report.findings
    assert back.fail_on == "warning"
