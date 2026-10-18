"""Finding model, ordering and exit-code threshold."""

from __future__ import annotations

import pytest

from usd_asset_gate.model import (
    Finding,
    Report,
    Severity,
    exit_code_for,
    is_failing,
    sort_findings,
)


def f(sev: Severity, file: str = "a.usda", rule: str = "r", prim: str | None = None) -> Finding:
    return Finding(rule=rule, code="C", severity=sev, file=file, message="m", prim_path=prim)


@pytest.mark.parametrize(
    "fail_on, severity, expected",
    [
        ("error", Severity.ERROR, True),
        ("error", Severity.WARNING, False),
        ("warning", Severity.WARNING, True),
        ("warning", Severity.INFO, False),
        ("info", Severity.INFO, True),
        ("never", Severity.ERROR, False),
    ],
)
def test_is_failing_threshold(fail_on, severity, expected):
    assert is_failing(f(severity), fail_on) is expected


def test_exit_code_for():
    assert exit_code_for([], "error") == 0
    assert exit_code_for([f(Severity.WARNING)], "error") == 0
    assert exit_code_for([f(Severity.WARNING)], "warning") == 1


def test_sort_is_file_then_most_severe_first():
    items = [
        f(Severity.INFO, "b.usda"),
        f(Severity.WARNING, "a.usda"),
        f(Severity.ERROR, "a.usda", prim="/Z"),
        f(Severity.ERROR, "a.usda", prim="/A"),
    ]
    ordered = sort_findings(items)
    assert [(x.file, x.severity, x.prim_path) for x in ordered] == [
        ("a.usda", Severity.ERROR, "/A"),
        ("a.usda", Severity.ERROR, "/Z"),
        ("a.usda", Severity.WARNING, None),
        ("b.usda", Severity.INFO, None),
    ]


def test_finding_round_trips_through_dict():
    original = Finding(
        rule="prim-budget",
        code="PrimBudgetExceeded",
        severity=Severity.WARNING,
        file="a.usda",
        message="too many",
        prim_path="/Root",
        layer="sub.usda",
        line=4,
        fixer_available=True,
    )
    data = original.to_dict()
    assert data["severity"] == "warning"
    assert Finding.from_dict(data) == original


def test_unknown_severity_is_rejected():
    with pytest.raises(ValueError, match="unknown severity"):
        Severity.parse("fatal")


def test_report_counts():
    report = Report(files=["a.usda"], findings=[f(Severity.ERROR), f(Severity.INFO)])
    assert report.counts() == {"info": 1, "warning": 0, "error": 1}
    assert report.exit_code == 1
