"""Runner: upstream selection, claim routing, severities, line numbers and the report."""

from __future__ import annotations

import os

import pytest

from conftest import FAIL_DIR, PASS_DIR
from usd_asset_gate import runner as runner_mod
from usd_asset_gate.config import Config, UpstreamConfig
from usd_asset_gate.model import Finding, Severity
from usd_asset_gate.rules import registry, upstream
from usd_asset_gate.rules.upstream import RawError, ValidatorInfo
from usd_asset_gate.runner import Runner, add_line_numbers
from usd_asset_gate.stage import AssetContext

MINIMAL = str(PASS_DIR / "minimal.usda")  # "Crate" on line 8, "Crate/Geo" on line 12
METADATA = "usdValidation:StageMetadataChecker"  # claimed by the default-prim alias


def info(name: str) -> ValidatorInfo:
    return ValidatorInfo(name=name, keywords=(), doc="", is_suite=False)


def raw(
    validator: str,
    name: str,
    prim_path: str | None = None,
    severity: Severity = Severity.ERROR,
) -> RawError:
    return RawError(validator, name, severity, f"{name} from {validator}", prim_path, None, False)


@pytest.fixture
def fake_upstream(monkeypatch):
    """Replace the validator registry and the validation run with fakes.

    ``state["errors"]`` is what the fake run returns (or raises, if an exception);
    ``state["calls"]`` records the validator names each run was asked for.
    """
    state = {"errors": [], "calls": []}
    monkeypatch.setattr(
        upstream, "all_validators", lambda: [info("v:A"), info("v:B"), info(METADATA)]
    )

    def run_validators(ctx, names):
        state["calls"].append(set(names))
        if isinstance(state["errors"], Exception):
            raise state["errors"]
        return list(state["errors"])

    monkeypatch.setattr(upstream, "run_validators", run_validators)
    return state


def only(*rule_ids: str, **rules: dict) -> dict[str, dict]:
    """Rule settings that disable every registry rule except ``rule_ids``."""
    settings = {r: {"enabled": False} for r in registry.BY_ID if r not in rule_ids}
    settings.update(rules)
    return settings


# --- add_line_numbers ----------------------------------------------------------


def test_add_line_numbers_fills_only_root_layer_prim_findings_without_a_line():
    ctx = AssetContext(path="minimal.usda", abs_path=MINIMAL)
    base = dict(rule="r", code="C", severity=Severity.WARNING, file="minimal.usda", message="m")
    findings = [
        Finding(**base, prim_path="/Crate/Geo"),
        Finding(**base, prim_path="/Crate.kind"),  # property paths use their prim
        Finding(**base, prim_path="/Crate", layer="other.usda"),  # spec is in another layer
        Finding(**base, prim_path="/Crate", line=99),  # already has a line
        Finding(**base, prim_path="/Missing"),  # not in the file
        Finding(**base),  # no prim at all
    ]
    assert add_line_numbers(ctx, findings) == [
        Finding(**base, prim_path="/Crate/Geo", line=12),
        Finding(**base, prim_path="/Crate.kind", line=8),
        Finding(**base, prim_path="/Crate", layer="other.usda"),
        Finding(**base, prim_path="/Crate", line=99),
        Finding(**base, prim_path="/Missing"),
        Finding(**base),
    ]
    assert [f.severity for f in add_line_numbers(ctx, findings)] == 6 * [Severity.WARNING]


def test_add_line_numbers_does_not_read_the_file_when_nothing_needs_a_line():
    ctx = AssetContext(path="gone.usda", abs_path="/nonexistent/gone.usda")
    findings = [Finding("r", "C", Severity.ERROR, "gone.usda", "m", prim_path="/A", line=3)]
    assert add_line_numbers(ctx, findings) is findings


def test_add_line_numbers_leaves_non_usda_files_alone(tmp_path):
    path = tmp_path / "asset.usdc"
    path.write_bytes(b"PXR-USDC" + b"\0" * 32)
    ctx = AssetContext(path="asset.usdc", abs_path=str(path))
    findings = [Finding("r", "C", Severity.ERROR, "asset.usdc", "m", prim_path="/Crate")]
    assert add_line_numbers(ctx, findings) is findings


# --- upstream selection --------------------------------------------------------


def test_disabled_upstream_selects_nothing_and_runs_only_claimed_validators(fake_upstream):
    config = Config(upstream=UpstreamConfig(enabled=False), rules=only("default-prim"))
    r = Runner(config)
    assert r.upstream_selected == []
    assert [v.name for v in r.available] == ["v:A", "v:B", METADATA]
    _, findings = r.check_file(MINIMAL)
    assert findings == []
    # the default-prim alias still needs its claimed validator run
    assert fake_upstream["calls"] == [{METADATA}]


def test_upstream_filters_select_validators(fake_upstream):
    config = Config(upstream=UpstreamConfig(validators=["v:*"], disable=["v:B"]))
    assert Runner(config).upstream_selected == ["v:A"]
    assert Runner(Config()).upstream_selected == ["v:A", "v:B", METADATA]


def test_per_rule_enabled_adds_or_removes_an_upstream_validator(fake_upstream):
    r = Runner(Config(upstream=UpstreamConfig(validators=["v:A"])))
    assert r._upstream_for({}) == {"v:A"}
    assert r._upstream_for({"upstream:v:A": {"enabled": False}}) == set()
    assert r._upstream_for({"upstream:v:B": {"enabled": True}}) == {"v:A", "v:B"}
    # settings without an explicit "enabled" leave the selection as it is
    assert r._upstream_for({"upstream:v:B": {"severity": "info"}}) == {"v:A"}
    assert r.upstream_selected == ["v:A"]  # not mutated


# --- check_file: routing upstream errors ---------------------------------------


def test_errors_go_to_the_claiming_alias_else_their_own_rule_else_nowhere(fake_upstream):
    fake_upstream["errors"] = [
        raw("v:A", "Selected", prim_path="/Crate/Geo"),
        raw("v:B", "NotSelected", prim_path="/Crate"),
        raw(METADATA, "MissingDefaultPrim"),
        raw(METADATA, "UnclaimedButNotSelected"),
    ]
    config = Config(upstream=UpstreamConfig(validators=["v:A"]), rules=only("default-prim"))
    ctx, findings = Runner(config).check_file(MINIMAL)
    assert fake_upstream["calls"] == [{"v:A", METADATA}]
    assert ctx.stage is not None
    assert findings == [
        Finding(
            rule="upstream:v:A",
            code="Selected",
            severity=Severity.ERROR,
            file=MINIMAL,
            message="Selected from v:A",
            prim_path="/Crate/Geo",
            line=12,
        ),
        Finding(
            rule="default-prim",
            code="MissingDefaultPrim",
            severity=Severity.ERROR,
            file=MINIMAL,
            message=f"MissingDefaultPrim from {METADATA}",
        ),
    ]


def test_disabling_an_alias_releases_its_claimed_errors(fake_upstream):
    fake_upstream["errors"] = [raw(METADATA, "MissingDefaultPrim")]
    # default-prim is disabled, so the error is reported under its upstream rule
    _, findings = Runner(Config(rules=only())).check_file(MINIMAL)
    assert [(f.rule, f.code) for f in findings] == [(f"upstream:{METADATA}", "MissingDefaultPrim")]


def test_configured_severity_overrides_upstream_and_alias_severity(fake_upstream):
    fake_upstream["errors"] = [
        raw("v:A", "Upstream", severity=Severity.ERROR),
        raw("v:B", "Untouched", severity=Severity.INFO),
        raw(METADATA, "MissingDefaultPrim", severity=Severity.ERROR),
    ]
    rules = only(
        "default-prim",
        **{"upstream:v:A": {"severity": "warning"}, "default-prim": {"severity": "info"}},
    )
    config = Config(upstream=UpstreamConfig(validators=["v:A", "v:B"]), rules=rules)
    _, findings = Runner(config).check_file(MINIMAL)
    assert [(f.rule, f.severity) for f in findings] == [
        ("upstream:v:A", Severity.WARNING),
        ("upstream:v:B", Severity.INFO),
        ("default-prim", Severity.INFO),
    ]


def test_validation_run_failure_is_a_finding_and_house_checks_still_run(fake_upstream):
    fake_upstream["errors"] = upstream.ValidationRunError("registry exploded")
    rules = only("default-prim", **{"default-prim": {"kind": "assembly"}})
    _, findings = Runner(Config(rules=rules)).check_file(MINIMAL)
    assert [(f.rule, f.code, f.line) for f in findings] == [
        ("upstream", "ValidationRunFailed", None),
        ("default-prim", "DefaultPrimKindMismatch", 8),
    ]
    assert findings[0].message == "OpenUSD could not run the validators: registry exploded"


def test_a_stage_that_cannot_open_skips_validation_and_house_checks(fake_upstream):
    path = str(FAIL_DIR / "unparseable" / "asset.usda")
    ctx, findings = Runner(Config()).check_file(path)
    assert ctx.stage is None
    assert [(f.rule, f.code, f.severity) for f in findings] == [
        (runner_mod.STAGE_OPEN_RULE, "StageOpenFailed", Severity.ERROR)
    ]
    assert findings[0].message
    assert fake_upstream["calls"] == []


# --- run: the report -----------------------------------------------------------


def test_run_sorts_findings_and_keeps_every_file(monkeypatch):
    def check_file(self, path):
        return AssetContext(path=path, abs_path=path), [
            Finding("r", "C", Severity.WARNING, path, "warn"),
            Finding("r", "C", Severity.ERROR, path, "err"),
        ]

    monkeypatch.setattr(upstream, "all_validators", lambda: [])
    monkeypatch.setattr(Runner, "check_file", check_file)
    report = Runner(Config()).run(["b.usda", "a.usda"])
    assert report.files == ["b.usda", "a.usda"]
    assert [(f.file, f.severity) for f in report.findings] == [
        ("a.usda", Severity.ERROR),
        ("a.usda", Severity.WARNING),
        ("b.usda", Severity.ERROR),
        ("b.usda", Severity.WARNING),
    ]


def test_run_fail_on_comes_from_the_argument_else_the_config(monkeypatch):
    monkeypatch.setattr(upstream, "all_validators", lambda: [])
    monkeypatch.setattr(
        Runner, "check_file", lambda self, path: (AssetContext(path=path, abs_path=path), [])
    )
    r = Runner(Config(fail_on="warning"))
    assert r.run([]).fail_on == "warning"
    assert r.run([], fail_on="never").fail_on == "never"
    assert Runner(Config()).run([]).fail_on == "error"


def test_run_keeps_diagnostics_per_file_relative_and_without_addresses(monkeypatch):
    cwd_prefix = os.getcwd().rstrip(os.sep) + os.sep

    def check_file(self, path):
        ctx = AssetContext(path=path, abs_path=path)
        if path == "noisy.usda":
            ctx.diagnostics = [f"Warning: stage <0x7f3a9c> could not open {cwd_prefix}x.usda"]
        return ctx, []

    monkeypatch.setattr(upstream, "all_validators", lambda: [])
    monkeypatch.setattr(Runner, "check_file", check_file)
    report = Runner(Config()).run(["quiet.usda", "noisy.usda"])
    assert report.diagnostics == {"noisy.usda": ["Warning: stage could not open x.usda"]}
