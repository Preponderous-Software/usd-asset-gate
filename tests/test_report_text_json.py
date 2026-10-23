"""Text and JSON reporters."""

from __future__ import annotations

import json

import pytest

from usd_asset_gate import __version__
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
    report = Report(
        files=["props/crate.usda", "b.usda"],
        findings=[FINDING],
        fail_on="warning",
        diagnostics={"props/crate.usda": ["Warning: could not open asset"]},
    )
    data = json.loads(json_report.render(report))
    assert data["files"] == [
        {"path": "props/crate.usda", "findings": 1, "diagnostics": ["Warning: could not open asset"]},
        {"path": "b.usda", "findings": 0, "diagnostics": []},
    ]
    back = json_report.from_dict(data)
    assert back.files == report.files
    assert back.findings == report.findings
    assert back.fail_on == "warning"
    assert back.diagnostics["props/crate.usda"] == ["Warning: could not open asset"]


def test_text_location_without_line_or_prim():
    finding = Finding(rule="r", code="C", severity=Severity.INFO, file="a.usda", message="m")
    assert text_report.format_finding(finding) == "a.usda: info r: m [C]"


def test_text_omits_layer_equal_to_file():
    finding = Finding(
        rule="r", code="C", severity=Severity.ERROR, file="a.usda", message="m", layer="a.usda"
    )
    assert text_report.format_finding(finding) == "a.usda: error r: m [C]"


def test_text_colour_per_severity():
    warning = Finding(rule="r", code="C", severity=Severity.WARNING, file="a.usda", message="m")
    info = Finding(rule="r", code="C", severity=Severity.INFO, file="a.usda", message="m")
    assert text_report.format_finding(warning, colour=True) == (
        "a.usda: \033[33mwarning\033[0m r: m [C]"
    )
    assert text_report.format_finding(info, colour=True) == "a.usda: \033[36minfo\033[0m r: m [C]"


def test_text_summary_pass_for_one_file():
    report = Report(files=["a.usda"], findings=[])
    assert text_report.summary_line(report) == (
        "PASS: 1 file checked, 0 error(s), 0 warning(s), 0 info (fail_on: error)"
    )


def test_text_summary_pass_when_findings_below_fail_on():
    warning = Finding(rule="r", code="C", severity=Severity.WARNING, file="a.usda", message="m")
    report = Report(files=["a.usda"], findings=[warning], fail_on="error")
    assert text_report.summary_line(report) == (
        "PASS: 1 file checked, 0 error(s), 1 warning(s), 0 info (fail_on: error)"
    )


def test_text_summary_zero_files_is_plural():
    report = Report(files=[], findings=[], fail_on="never")
    assert text_report.summary_line(report) == (
        "PASS: 0 files checked, 0 error(s), 0 warning(s), 0 info (fail_on: never)"
    )


def test_text_render_one_line_per_finding():
    info = Finding(rule="r", code="C", severity=Severity.INFO, file="b.usda", message="m")
    report = Report(files=["props/crate.usda", "b.usda"], findings=[FINDING, info])
    out = text_report.render(report)
    assert out.endswith("\n")
    assert out.splitlines() == [
        text_report.format_finding(FINDING),
        text_report.format_finding(info),
        "FAIL: 2 files checked, 1 error(s), 0 warning(s), 1 info (fail_on: error)",
    ]


def test_use_colour_needs_a_tty(monkeypatch):
    class Pipe:
        def isatty(self):
            return False

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert not text_report.use_colour(Pipe())
    assert not text_report.use_colour(object())


def test_json_summary_block():
    warning = Finding(rule="r", code="C", severity=Severity.WARNING, file="b.usda", message="m")
    report = Report(
        files=["props/crate.usda", "b.usda"], findings=[FINDING, warning], fail_on="warning"
    )
    data = json_report.to_dict(report)
    assert data["version"] == 1
    assert data["tool"] == {"name": "usd-asset-gate", "version": __version__}
    assert data["summary"] == {
        "files": 2,
        "error": 1,
        "warning": 1,
        "info": 0,
        "fail_on": "warning",
        "exit_code": 1,
        "passed": False,
    }


def test_json_summary_passed_when_clean():
    summary = json_report.to_dict(Report(files=["a.usda"], findings=[]))["summary"]
    assert summary["exit_code"] == 0
    assert summary["passed"] is True


def test_json_counts_findings_for_unlisted_file():
    report = Report(files=["b.usda"], findings=[FINDING])
    assert json_report.to_dict(report)["files"] == [
        {"path": "b.usda", "findings": 0, "diagnostics": []},
        {"path": "props/crate.usda", "findings": 1, "diagnostics": []},
    ]


def test_json_findings_serialise_every_field():
    assert json_report.to_dict(Report(files=[], findings=[FINDING]))["findings"] == [
        {
            "rule": "default-prim",
            "code": "MissingDefaultPrim",
            "severity": "error",
            "file": "props/crate.usda",
            "message": "no default prim",
            "prim_path": "/Crate",
            "layer": None,
            "line": 7,
            "fixer_available": False,
        }
    ]


def test_json_render_is_indented_with_trailing_newline():
    out = json_report.render(Report(files=[], findings=[]))
    assert out.startswith('{\n  "version": 1,')
    assert out.endswith("}\n")


def test_json_from_dict_rejects_other_versions():
    data = json_report.to_dict(Report(files=[], findings=[]))
    data["version"] = 2
    with pytest.raises(ValueError, match=r"unsupported report version 2 \(expected 1\)"):
        json_report.from_dict(data)
    del data["version"]
    with pytest.raises(ValueError, match="unsupported report version None"):
        json_report.from_dict(data)


def test_json_from_dict_defaults_missing_diagnostics():
    data = json_report.to_dict(Report(files=["a.usda"], findings=[]))
    del data["files"][0]["diagnostics"]
    assert json_report.from_dict(data).diagnostics == {"a.usda": []}
