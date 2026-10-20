"""Rule registry: specs, claims, the dependency aliases and the rule tables."""

from __future__ import annotations

import os

from pxr import Usd

from usd_asset_gate.model import Finding, Severity
from usd_asset_gate.rules import registry
from usd_asset_gate.rules.registry import Claim, RuleSpec
from usd_asset_gate.rules.upstream import RawError
from usd_asset_gate.stage import AssetContext, open_stage

COMPOSITION = "usdValidation:CompositionErrorTest"
MISSING_REF = "usdUtilsValidators:MissingReferenceValidator"


def raw(
    validator: str = MISSING_REF,
    name: str = "UnresolvableDependency",
    message: str = "Found unresolvable external dependency 'gone.usda'.",
    prim_path: str | None = None,
    layer: str | None = None,
    fixer_available: bool = False,
    severity: Severity = Severity.ERROR,
) -> RawError:
    return RawError(validator, name, severity, message, prim_path, layer, fixer_available)


def mem_ctx(path: str = "mem.usda") -> AssetContext:
    return AssetContext(path=path, abs_path=path, stage=Usd.Stage.CreateInMemory())


def test_claim_matches_validator_and_code_only():
    claim = Claim("v:A", ("X", "Y"))
    assert claim.matches(raw(validator="v:A", name="Y"), None)
    assert not claim.matches(raw(validator="v:B", name="X"), None)
    assert not claim.matches(raw(validator="v:A", name="Z"), None)


def test_claim_applies_receives_error_and_context():
    seen = []

    def applies(error, ctx):
        seen.append((error, ctx))
        return error.message == "take"

    claim = Claim("v:A", ("X",), applies)
    ctx = object()
    take, leave = raw("v:A", "X", "take"), raw("v:A", "X", "leave")
    assert claim.matches(take, ctx)
    assert not claim.matches(leave, ctx)
    assert seen == [(take, ctx), (leave, ctx)]
    # applies is not consulted when validator or code already differ
    assert not claim.matches(raw("v:B", "X", "take"), ctx)
    assert len(seen) == 2


def test_rule_spec_settings_schema_and_claimed_validators():
    spec = RuleSpec(
        id="sample",
        kind="alias",
        description="A sample rule.",
        severity=Severity.WARNING,
        params={"limit": {"type": "integer"}},
        defaults={"limit": 3, "enabled": False},
        claims=(Claim("v:A", ("X",)), Claim("v:B", ("Y",)), Claim("v:A", ("Z",))),
    )
    assert spec.claimed_validators == {"v:A", "v:B"}
    # defaults are merged last, so they override the built-in keys
    assert spec.default_settings() == {"enabled": False, "severity": "warning", "limit": 3}
    assert spec.schema() == {
        "type": "object",
        "description": "A sample rule.",
        "additionalProperties": False,
        "properties": {
            "enabled": {"type": "boolean"},
            "severity": {"enum": ["info", "warning", "error"]},
            "limit": {"type": "integer"},
        },
    }


def test_composition_errors_are_split_by_message():
    refs, subs = registry.get("unresolved-references"), registry.get("unresolved-sublayers")
    ctx = mem_ctx()
    sublayer = raw(COMPOSITION, "CompositionError", "Could not load sublayer @a.usda@")
    reference = raw(COMPOSITION, "CompositionError", "@b.usda@ for reference introduced by x")
    payload = raw(COMPOSITION, "CompositionError", "@c.usda@ for payload introduced by x")
    other = raw(COMPOSITION, "CompositionError", "some other composition problem")

    def owners(error):
        return [s.id for s in (refs, subs) if any(c.matches(error, ctx) for c in s.claims)]

    assert owners(sublayer) == ["unresolved-sublayers"]
    assert owners(reference) == ["unresolved-references"]
    assert owners(payload) == ["unresolved-references"]
    assert owners(other) == []


def test_unresolved_sublayer_paths_lists_every_sublayer_relative_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "root.usda").write_text(
        '#usda 1.0\n(\n    subLayers = [@present.usda@, @missing.usda@]\n)\n', encoding="utf-8"
    )
    (tmp_path / "present.usda").write_text(
        '#usda 1.0\n(\n    subLayers = [@deeper/gone.usda@]\n)\n', encoding="utf-8"
    )
    ctx = AssetContext(path="root.usda", abs_path=os.path.join(os.getcwd(), "root.usda"))
    assert open_stage(ctx) == []
    # resolvable sublayers are listed too, not only the unresolved ones
    assert registry.unresolved_sublayer_paths(ctx) == {
        "present.usda",
        "missing.usda",
        os.path.join("deeper", "gone.usda"),
    }

    refs, subs = registry.get("unresolved-references"), registry.get("unresolved-sublayers")
    as_sublayer = raw(message="Found unresolvable external dependency 'missing.usda'.")
    as_reference = raw(message="Found unresolvable external dependency 'other.usda'.")
    for error, owner in ((as_sublayer, subs), (as_reference, refs)):
        assert [s for s in (refs, subs) if any(c.matches(error, ctx) for c in s.claims)] == [
            owner
        ]


def test_dependency_report_groups_by_path_in_sorted_order():
    spec = registry.get("unresolved-references")
    ctx = mem_ctx("asset.usda")
    errors = [
        raw(message="Found unresolvable external dependency 'z.usda'.", layer="L1"),
        raw(
            COMPOSITION,
            "CompositionError",
            "Could not open asset @z.usda@ for reference introduced by @asset.usda@<</A>>",
            prim_path="/A",
            layer="L2",
            fixer_available=True,
        ),
        raw(message="Found unresolvable external dependency 'a.usda'."),
        raw(COMPOSITION, "CompositionError", "no asset path here"),
    ]
    findings = spec.report_claimed(spec, ctx, errors, Severity.WARNING)
    assert findings == [
        Finding(
            rule="unresolved-references",
            code="UnresolvableDependency",
            severity=Severity.WARNING,
            file="asset.usda",
            message="reference 'a.usda' cannot be resolved",
        ),
        # an error with no path in it is grouped under its whole message
        Finding(
            rule="unresolved-references",
            code="UnresolvableDependency",
            severity=Severity.WARNING,
            file="asset.usda",
            message="reference 'no asset path here' cannot be resolved",
        ),
        # both validators saw z.usda: one finding, first prim/layer, any fixer
        Finding(
            rule="unresolved-references",
            code="UnresolvableDependency",
            severity=Severity.WARNING,
            file="asset.usda",
            message="reference 'z.usda' cannot be resolved (introduced at </A>)",
            prim_path="/A",
            layer="L1",
            fixer_available=True,
        ),
    ]


def test_sublayer_report_uses_its_own_label():
    spec = registry.get("unresolved-sublayers")
    errors = [raw(COMPOSITION, "CompositionError", "Could not load sublayer @s.usda@")]
    (finding,) = spec.report_claimed(spec, mem_ctx(), errors, Severity.ERROR)
    assert finding.rule == "unresolved-sublayers"
    assert finding.message == "sublayer 's.usda' cannot be resolved"


def test_report_each_keeps_the_error_but_takes_rule_and_severity_from_the_alias():
    spec = registry.get("stage-metadata")
    error = raw(
        "usdGeomValidators:StageMetadataChecker",
        "MissingUpAxisMetadata",
        "Stage does not specify an upAxis.",
        prim_path="/Root",
        layer="asset.usda",
        fixer_available=True,
        severity=Severity.ERROR,
    )
    findings = spec.report_claimed(spec, mem_ctx("asset.usda"), [error, error], Severity.INFO)
    assert findings == 2 * [
        Finding(
            rule="stage-metadata",
            code="MissingUpAxisMetadata",
            severity=Severity.INFO,
            file="asset.usda",
            message="Stage does not specify an upAxis.",
            prim_path="/Root",
            layer="asset.usda",
            fixer_available=True,
        )
    ]


def test_rule_tables_and_lookup():
    assert registry.RULES == registry.ALIASES + registry.HOUSE
    ids = [r.id for r in registry.RULES]
    assert ids == [
        "unresolved-references",
        "unresolved-sublayers",
        "default-prim",
        "stage-metadata",
        "file-size",
        "prim-budget",
        "instance-budget",
        "prim-naming",
    ]
    assert list(registry.BY_ID) == ids
    assert all(registry.get(i) is registry.BY_ID[i] for i in ids)
    assert registry.get("stage-open") is None
    assert registry.get("upstream:usdValidation:StageMetadataChecker") is None


def test_aliases_claim_and_report_while_house_rules_only_check():
    for spec in registry.ALIASES:
        assert spec.kind == "alias"
        assert spec.claims and spec.report_claimed is not None
    for spec in registry.HOUSE:
        assert spec.kind == "house"
        assert not spec.claims and spec.report_claimed is None
        assert (spec.check is None) != (spec.check_before_open is None)
    assert registry.get("file-size").check_before_open is not None
    assert registry.get("prim-naming").validate_settings is not None


def test_default_severities():
    severities = {r.id: r.default_settings()["severity"] for r in registry.RULES}
    warnings = {"prim-budget", "instance-budget", "prim-naming"}
    assert {i for i, s in severities.items() if s == "warning"} == warnings
    assert all(s == "error" for i, s in severities.items() if i not in warnings)
