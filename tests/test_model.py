"""Finding model, ordering and exit-code threshold."""

from __future__ import annotations

import dataclasses

import pytest

from usd_asset_gate.model import (
    FAIL_ON_VALUES,
    Finding,
    Report,
    Severity,
    exit_code_for,
    is_failing,
    severity_of,
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


# --- severities, thresholds and settings ---


def test_severity_ranks_order_info_below_warning_below_error():
    assert [s.rank for s in (Severity.INFO, Severity.WARNING, Severity.ERROR)] == [0, 1, 2]
    assert Severity.ERROR == "error"  # a str enum: compares equal to its value


@pytest.mark.parametrize("value", ["error", "warning", "info"])
def test_severity_parse_accepts_each_value(value):
    assert Severity.parse(value) is Severity(value)


def test_severity_parse_error_lists_the_choices_and_hides_the_enum_error():
    with pytest.raises(ValueError) as excinfo:
        Severity.parse("Error")  # case-sensitive
    assert str(excinfo.value) == (
        "unknown severity 'Error' (expected one of: error, warning, info)"
    )
    assert excinfo.value.__suppress_context__


def test_fail_on_values_are_the_severities_plus_never():
    assert FAIL_ON_VALUES == ("error", "warning", "info", "never")


def test_is_failing_rejects_an_unknown_threshold():
    with pytest.raises(ValueError, match="unknown severity 'fatal'"):
        is_failing(f(Severity.ERROR), "fatal")


def test_severity_of_uses_the_setting_or_the_default():
    assert severity_of({}, Severity.WARNING) is Severity.WARNING
    assert severity_of({"severity": None}, Severity.WARNING) is Severity.WARNING
    assert severity_of({"severity": ""}, Severity.ERROR) is Severity.ERROR
    assert severity_of({"severity": "info"}, Severity.ERROR) is Severity.INFO
    with pytest.raises(ValueError, match="unknown severity 'loud'"):
        severity_of({"severity": "loud"}, Severity.ERROR)


def test_exit_code_for_accepts_a_generator_and_never():
    assert exit_code_for((x for x in [f(Severity.INFO)]), "info") == 1
    assert exit_code_for([f(Severity.ERROR)], "never") == 0


# --- findings: serialisation, immutability and ordering ---


def test_finding_from_dict_fills_optional_fields_with_defaults():
    finding = Finding.from_dict(
        {"rule": "r", "code": "C", "severity": "info", "file": "a.usda", "message": "m"}
    )
    assert finding == Finding(
        rule="r", code="C", severity=Severity.INFO, file="a.usda", message="m"
    )
    assert (finding.prim_path, finding.layer, finding.line, finding.fixer_available) == (
        None,
        None,
        None,
        False,
    )


def test_finding_from_dict_coerces_fixer_available_to_bool():
    base = {"rule": "r", "code": "C", "severity": "error", "file": "a.usda", "message": "m"}
    assert Finding.from_dict({**base, "fixer_available": 1}).fixer_available is True
    assert Finding.from_dict({**base, "fixer_available": None}).fixer_available is False


def test_finding_from_dict_rejects_an_unknown_severity():
    with pytest.raises(ValueError, match="unknown severity 'fatal'"):
        Finding.from_dict(
            {"rule": "r", "code": "C", "severity": "fatal", "file": "a.usda", "message": "m"}
        )


def test_finding_to_dict_has_every_field_and_a_plain_string_severity():
    data = f(Severity.ERROR).to_dict()
    assert list(data) == [
        "rule",
        "code",
        "severity",
        "file",
        "message",
        "prim_path",
        "layer",
        "line",
        "fixer_available",
    ]
    assert type(data["severity"]) is str and data["severity"] == "error"


def test_finding_is_immutable():
    finding = f(Severity.INFO)
    with pytest.raises(dataclasses.FrozenInstanceError):
        finding.message = "changed"


def test_sort_breaks_ties_by_rule_then_prim_then_code_then_message():
    def g(rule, prim, code, message):
        return Finding(
            rule=rule,
            code=code,
            severity=Severity.ERROR,
            file="a.usda",
            message=message,
            prim_path=prim,
        )

    items = [
        g("z-rule", None, "A", "a"),
        g("a-rule", "/B", "A", "a"),
        g("a-rule", "/A", "B", "a"),
        g("a-rule", "/A", "A", "b"),
        g("a-rule", "/A", "A", "a"),
        g("a-rule", None, "Z", "z"),  # no prim sorts before any prim path
    ]
    ordered = sort_findings(items)
    assert [(x.rule, x.prim_path, x.code, x.message) for x in ordered] == [
        ("a-rule", None, "Z", "z"),
        ("a-rule", "/A", "A", "a"),
        ("a-rule", "/A", "A", "b"),
        ("a-rule", "/A", "B", "a"),
        ("a-rule", "/B", "A", "a"),
        ("z-rule", None, "A", "a"),
    ]
    assert ordered is not items and items[0].rule == "z-rule"  # input list left untouched


# --- reports ---


def test_report_defaults_and_independent_diagnostics():
    first, second = Report(files=[], findings=[]), Report(files=[], findings=[])
    assert first.fail_on == "error"
    first.diagnostics["a.usda"] = ["warning text"]
    assert second.diagnostics == {}


def test_report_counts_are_zero_for_every_severity_when_clean():
    report = Report(files=["a.usda"], findings=[])
    assert report.counts() == {"info": 0, "warning": 0, "error": 0}
    assert report.exit_code == 0


def test_report_exit_code_follows_fail_on():
    findings = [f(Severity.WARNING)]
    assert Report(files=["a.usda"], findings=findings).exit_code == 0
    assert Report(files=["a.usda"], findings=findings, fail_on="warning").exit_code == 1
    assert Report(files=["a.usda"], findings=[f(Severity.ERROR)], fail_on="never").exit_code == 0


def test_report_findings_for_matches_the_exact_path_in_order():
    a1 = f(Severity.INFO, "a.usda", rule="first")
    b = f(Severity.ERROR, "b.usda")
    a2 = f(Severity.ERROR, "a.usda", rule="second")
    report = Report(files=["a.usda", "b.usda"], findings=[a1, b, a2])
    assert report.findings_for("a.usda") == [a1, a2]
    assert report.findings_for("b.usda") == [b]
    assert report.findings_for("A.usda") == []
    assert report.findings_for("sub/a.usda") == []
