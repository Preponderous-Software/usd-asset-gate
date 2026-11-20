"""House rules: prim-budget, instance-budget, prim-naming."""

from __future__ import annotations

import pytest
from pxr import Usd, UsdUtils

from usd_asset_gate import config as cfg
from usd_asset_gate.model import Finding, Severity
from usd_asset_gate.rules.house import instance_budget, prim_budget, prim_naming
from usd_asset_gate.runner import Runner
from usd_asset_gate.stage import AssetContext


def ctx_for(stage) -> AssetContext:
    return AssetContext(path="mem.usda", abs_path="mem.usda", stage=stage)


def stage_with_prims(n: int, type_name: str = "Xform") -> Usd.Stage:
    """A stage with exactly ``n`` prims: one root plus ``n - 1`` children."""
    stage = Usd.Stage.CreateInMemory()
    root = stage.DefinePrim("/Root", "Xform")
    stage.SetDefaultPrim(root)
    for i in range(n - 1):
        stage.DefinePrim(f"/Root/P{i}", type_name)
    return stage


# --- prim-budget --------------------------------------------------------------------


def test_max_prims_passes_at_limit_and_fails_one_over():
    assert prim_budget.check(ctx_for(stage_with_prims(100)), {"max_prims": 100}) == []
    (f,) = prim_budget.check(ctx_for(stage_with_prims(101)), {"max_prims": 100})
    assert (f.rule, f.code, f.severity) == ("prim-budget", "PrimBudgetExceeded", Severity.WARNING)
    assert f.message == "101 prims; the budget is 100"


def test_max_by_type_counts_only_that_type():
    stage = stage_with_prims(1)
    for i in range(3):
        stage.DefinePrim(f"/Root/M{i}", "Mesh")
    for i in range(10):
        stage.DefinePrim(f"/Root/X{i}", "Xform")
    assert prim_budget.check(ctx_for(stage), {"max_by_type": {"Mesh": 3}}) == []
    (f,) = prim_budget.check(ctx_for(stage), {"max_by_type": {"Mesh": 2}})
    assert (f.code, f.message) == ("PrimTypeBudgetExceeded", "3 Mesh prims; the budget is 2")
    # A type that is absent counts as zero.
    assert prim_budget.check(ctx_for(stage), {"max_by_type": {"Camera": 0}}) == []


def test_type_counts_include_prototypes():
    stats = {
        "primary": {"primCountsByType": {"Mesh": 2, "Xform": 1}},
        "prototypes": {"primCountsByType": {"Mesh": 3}},
    }
    assert prim_budget.counts_by_type(stats) == {"Mesh": 5, "Xform": 1}


def test_nothing_configured_checks_nothing():
    assert prim_budget.check(ctx_for(stage_with_prims(500)), {}) == []


# --- instance-budget ----------------------------------------------------------------


def instanced_stage(n_instanced: int, n_plain: int) -> Usd.Stage:
    stage = Usd.Stage.CreateInMemory()
    world = stage.DefinePrim("/World", "Xform")
    Usd.ModelAPI(world).SetKind("assembly")
    stage.SetDefaultPrim(world)
    proto = stage.CreateClassPrim("/Proto")
    stage.DefinePrim("/Proto/Geo", "Cube")
    for i in range(n_instanced):
        p = stage.DefinePrim(f"/World/I{i}", "Xform")
        Usd.ModelAPI(p).SetKind("component")
        p.GetReferences().AddInternalReference(proto.GetPath())
        p.SetInstanceable(True)
    for i in range(n_plain):
        p = stage.DefinePrim(f"/World/P{i}", "Xform")
        Usd.ModelAPI(p).SetKind("component")
        p.GetReferences().AddInternalReference(proto.GetPath())
    return stage


def test_min_instance_ratio_boundary():
    stage = instanced_stage(3, 1)
    stats = UsdUtils.ComputeUsdStageStats(stage)
    ratio = stats["instancedModelCount"] / stats["modelCount"]
    assert instance_budget.check(ctx_for(stage), {"min_instance_ratio": ratio}) == []
    (f,) = instance_budget.check(ctx_for(instanced_stage(3, 1)), {"min_instance_ratio": ratio + 0.01})
    assert f.code == "InstanceRatioTooLow"


def test_max_prototypes():
    stage = instanced_stage(2, 0)
    assert instance_budget.check(ctx_for(stage), {"max_prototypes": 1}) == []
    (f,) = instance_budget.check(ctx_for(instanced_stage(2, 0)), {"max_prototypes": 0})
    assert (f.code, f.message) == (
        "PrototypeBudgetExceeded",
        "1 instancing prototypes; the budget is 0",
    )


def test_ratio_is_skipped_without_models():
    assert instance_budget.check(ctx_for(stage_with_prims(5)), {"min_instance_ratio": 1.0}) == []


def test_stats_are_computed_once_per_file(monkeypatch, tmp_path):
    calls = []
    real = UsdUtils.ComputeUsdStageStats

    def counting(stage):
        calls.append(stage)
        return real(stage)

    monkeypatch.setattr(UsdUtils, "ComputeUsdStageStats", counting)
    path = tmp_path / "set.usda"
    stage = instanced_stage(2, 2)  # keep the stage alive: Sdf.Layer handles are weak
    stage.GetRootLayer().Export(str(path))
    config = cfg.parse(
        {
            "rules": {
                "prim-budget": {"max_prims": 1, "max_by_type": {"Cube": 0}},
                "instance-budget": {"max_prototypes": 0, "min_instance_ratio": 0.9},
            }
        }
    )
    report = Runner(config).run([str(path)])
    codes = sorted(f.code for f in report.findings if f.rule.endswith("-budget"))
    assert codes == [
        "InstanceRatioTooLow",
        "PrimBudgetExceeded",
        "PrimTypeBudgetExceeded",
        "PrototypeBudgetExceeded",
    ]
    assert len(calls) == 1


# --- prim-naming --------------------------------------------------------------------

MATERIAL_THEN_ANY = {
    "patterns": [
        {"match": {"type": "Material"}, "regex": "^M_[A-Za-z0-9_]+$"},
        {"match": {"type": "*"}, "regex": "^[A-Z]"},
    ]
}


def naming_stage(*prims: tuple[str, str]) -> Usd.Stage:
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim("/Root", "Xform")
    for path, type_name in prims:
        stage.DefinePrim(path, type_name)
    return stage


def test_material_named_wood_fails_and_m_wood_passes():
    stage = naming_stage(("/Root/wood", "Material"), ("/Root/M_wood", "Material"))
    (f,) = prim_naming.check(ctx_for(stage), MATERIAL_THEN_ANY)
    assert (f.code, f.prim_path) == ("PrimNameMismatch", "/Root/wood")


def test_first_matching_pattern_wins():
    # "m_x" is a Material, so only the Material pattern is consulted; the catch-all
    # ^[A-Z] pattern after it never is.
    stage = naming_stage(("/Root/m_x", "Material"))
    (f,) = prim_naming.check(ctx_for(stage), MATERIAL_THEN_ANY)
    assert "^M_" in f.message


def test_untyped_prims_match_star_and_unmatched_prims_are_skipped():
    stage = naming_stage(("/Root/lower", ""))
    (f,) = prim_naming.check(ctx_for(stage), MATERIAL_THEN_ANY)
    assert "untyped prim name 'lower'" in f.message
    only_materials = {"patterns": [MATERIAL_THEN_ANY["patterns"][0]]}
    assert prim_naming.check(ctx_for(naming_stage(("/Root/lower", "Xform"))), only_materials) == []


def test_path_glob_match():
    settings = {"patterns": [{"match": {"path": "/Root/Geo/**"}, "regex": "^geo_"}]}
    stage = naming_stage(("/Root/Geo", "Scope"), ("/Root/Geo/geo_a", "Mesh"), ("/Root/Geo/b", "Mesh"), ("/Root/c", "Mesh"))
    assert [f.prim_path for f in prim_naming.check(ctx_for(stage), settings)] == ["/Root/Geo/b"]


def test_findings_are_capped_with_a_summary():
    stage = naming_stage(*[(f"/Root/p{i}", "Xform") for i in range(5)])
    findings = prim_naming.check(
        ctx_for(stage), {"patterns": [{"regex": "^[A-Z]"}], "max_findings": 2}
    )
    assert [f.code for f in findings] == ["PrimNameMismatch"] * 2 + ["PrimNamingFindingsCapped"]
    assert findings[-1].severity is Severity.INFO
    assert "3 more" in findings[-1].message


def test_invalid_regex_is_a_config_error_naming_the_key():
    with pytest.raises(cfg.ConfigError, match=r"rules\.prim-naming\.patterns\[1\]\.regex"):
        cfg.parse({"rules": {"prim-naming": {"patterns": [{"regex": "ok"}, {"regex": "(["}]}}})
    with pytest.raises(cfg.ConfigError, match=r"overrides\[0\]\.rules\.prim-naming\.patterns\[0\]\.regex"):
        cfg.parse(
            {"overrides": [{"paths": ["a/**"], "rules": {"prim-naming": {"patterns": [{"regex": "("}]}}}]}
        )


@pytest.mark.parametrize(
    "rules, key",
    [
        ({"prim-budget": {"max_prims": "lots"}}, "rules.prim-budget.max_prims: expected integer"),
        ({"prim-budget": {"max_by_type": {"Mesh": -1}}}, "rules.prim-budget.max_by_type.Mesh: must be >= 0"),
        ({"instance-budget": {"min_instance_ratio": 1.5}}, "rules.instance-budget.min_instance_ratio: must be <= 1"),
        ({"prim-naming": {"patterns": [{"match": {"type": "Mesh"}}]}}, "rules.prim-naming.patterns[0].regex: required"),
        ({"prim-naming": {"patterns": [{"regex": "x", "match": {"kind": "a"}}]}}, "rules.prim-naming.patterns[0].match.kind: unknown key"),
    ],
)
def test_invalid_budget_settings_name_the_key(rules, key):
    with pytest.raises(cfg.ConfigError) as exc:
        cfg.parse({"rules": rules})
    assert key in str(exc.value)


def test_empty_path_glob_matches_nothing():
    settings = {
        "patterns": [
            {"match": {"path": ""}, "regex": ".*"},
            {"regex": "^[A-Z]"},
        ]
    }
    stage = naming_stage(("/Root/lower", "Xform"))
    assert [f.prim_path for f in prim_naming.check(ctx_for(stage), settings)] == ["/Root/lower"]


def test_oversized_repetition_is_a_config_error():
    with pytest.raises(cfg.ConfigError, match=r"rules\.prim-naming\.patterns\[0\]\.regex"):
        cfg.parse({"rules": {"prim-naming": {"patterns": [{"regex": "a{99999999999999999999}"}]}}})


# --- budgets on precomputed stats ---------------------------------------------------


def stats_ctx(stats: dict) -> AssetContext:
    """A context whose stats are given: no stage is opened or measured."""
    return AssetContext(path="mem.usda", abs_path="mem.usda", _stats=stats)


def test_prim_budget_reports_total_then_types_in_name_order_with_override():
    stats = {
        "totalPrimCount": 12,
        "primary": {"primCountsByType": {"Mesh": 4, "Xform": 8}},
        "prototypes": {"primCountsByType": {"Mesh": 1}},
    }
    settings = {"max_prims": 10, "max_by_type": {"Xform": 7, "Mesh": 4}, "severity": "error"}
    findings = prim_budget.check(stats_ctx(stats), settings)
    assert [(f.code, f.message) for f in findings] == [
        ("PrimBudgetExceeded", "12 prims; the budget is 10"),
        ("PrimTypeBudgetExceeded", "5 Mesh prims; the budget is 4"),
        ("PrimTypeBudgetExceeded", "8 Xform prims; the budget is 7"),
    ]
    assert {(f.rule, f.severity, f.file, f.prim_path) for f in findings} == {
        ("prim-budget", Severity.ERROR, "mem.usda", None)
    }


def test_prim_budget_type_limits_at_their_count_pass():
    stats = {"totalPrimCount": 3, "primary": {"primCountsByType": {"Mesh": 3}}}
    assert prim_budget.check(stats_ctx(stats), {"max_prims": 3, "max_by_type": {"Mesh": 3}}) == []


def test_type_counts_tolerate_missing_sections():
    assert prim_budget.counts_by_type({}) == {}
    assert prim_budget.counts_by_type({"prototypes": {"primCountsByType": {"Cube": 2}}}) == {"Cube": 2}


def test_instance_budget_reports_both_findings_with_override():
    stats = {"prototypeCount": 3, "modelCount": 4, "instancedModelCount": 1}
    settings = {"max_prototypes": 2, "min_instance_ratio": 0.5, "severity": "info"}
    findings = instance_budget.check(stats_ctx(stats), settings)
    assert [(f.code, f.message) for f in findings] == [
        ("PrototypeBudgetExceeded", "3 instancing prototypes; the budget is 2"),
        ("InstanceRatioTooLow", "1 of 4 models are instanced (0.25); the minimum ratio is 0.5"),
    ]
    assert {(f.rule, f.severity, f.file) for f in findings} == {
        ("instance-budget", Severity.INFO, "mem.usda")
    }


def test_instance_budget_limits_at_their_values_pass():
    stats = {"prototypeCount": 2, "modelCount": 4, "instancedModelCount": 2}
    settings = {"max_prototypes": 2, "min_instance_ratio": 0.5}
    assert instance_budget.check(stats_ctx(stats), settings) == []


def test_instance_budget_nothing_configured_checks_nothing():
    # Stats are never read: an empty dict would raise KeyError if they were.
    assert instance_budget.check(stats_ctx({}), {}) == []
    assert prim_budget.check(stats_ctx({}), {"max_by_type": {}}) == []


# --- prim-naming: matching, finding fields and the cap ------------------------------


def test_mismatch_finding_names_type_name_and_regex():
    stage = naming_stage(("/Root/wood", "Material"))
    (f,) = prim_naming.check(ctx_for(stage), MATERIAL_THEN_ANY)
    assert f == Finding(
        rule="prim-naming",
        code="PrimNameMismatch",
        severity=Severity.WARNING,
        file="mem.usda",
        message="Material prim name 'wood' does not match ^M_[A-Za-z0-9_]+$",
        prim_path="/Root/wood",
    )


def test_type_glob_supports_wildcards():
    settings = {"patterns": [{"match": {"type": "Mat*"}, "regex": "^M_"}]}
    stage = naming_stage(("/Root/wood", "Material"), ("/Root/geo", "Mesh"))
    assert [f.prim_path for f in prim_naming.check(ctx_for(stage), settings)] == ["/Root/wood"]


def test_type_and_path_must_both_match():
    settings = {"patterns": [{"match": {"type": "Mesh", "path": "/Root/Geo/**"}, "regex": "^geo_"}]}
    stage = naming_stage(
        ("/Root/Geo", "Scope"),
        ("/Root/Geo/a", "Mesh"),
        ("/Root/Geo/b", "Xform"),
        ("/Root/c", "Mesh"),
    )
    assert [f.prim_path for f in prim_naming.check(ctx_for(stage), settings)] == ["/Root/Geo/a"]


def test_severity_override_applies_to_mismatches_but_not_the_cap_summary():
    stage = naming_stage(*[(f"/Root/p{i}", "Xform") for i in range(3)])
    settings = {"patterns": [{"regex": "^[A-Z]"}], "max_findings": 2, "severity": "error"}
    findings = prim_naming.check(ctx_for(stage), settings)
    assert [f.severity for f in findings] == [Severity.ERROR, Severity.ERROR, Severity.INFO]


def test_findings_exactly_at_the_cap_add_no_summary():
    stage = naming_stage(*[(f"/Root/p{i}", "Xform") for i in range(2)])
    findings = prim_naming.check(ctx_for(stage), {"patterns": [{"regex": "^[A-Z]"}], "max_findings": 2})
    assert [f.code for f in findings] == ["PrimNameMismatch"] * 2


def test_default_cap_is_fifty():
    stage = naming_stage(*[(f"/Root/p{i}", "Xform") for i in range(prim_naming.DEFAULT_MAX_FINDINGS + 1)])
    findings = prim_naming.check(ctx_for(stage), {"patterns": [{"regex": "^[A-Z]"}]})
    assert prim_naming.DEFAULT_MAX_FINDINGS == 50
    assert len(findings) == 51
    assert findings[-1].code == "PrimNamingFindingsCapped"
    assert findings[-1].message == (
        "1 more prim name(s) do not match; only the first 50 are listed "
        "(raise prim-naming.max_findings to see more)"
    )
    assert findings[-1].prim_path is None


def test_no_patterns_checks_nothing():
    stage = naming_stage(("/Root/lower", "Xform"))
    assert prim_naming.check(ctx_for(stage), {}) == []
    assert prim_naming.check(ctx_for(stage), {"patterns": []}) == []


def test_compile_patterns_keeps_type_and_compiles_path_and_regex():
    (any_prim, mesh) = prim_naming.compile_patterns(
        [{"regex": "^a"}, {"match": {"type": "Mesh", "path": "/A/**"}, "regex": "^b"}]
    )
    assert any_prim[0] is None and any_prim[1] is None and any_prim[2].pattern == "^a"
    assert mesh[0] == "Mesh" and mesh[2].pattern == "^b"
    assert mesh[1].match("/A/x") and not mesh[1].match("/B/x")


def test_class_prims_and_instance_proxies_are_not_visited():
    # Cubes exist under the /Proto class, under the instance /World/I0 (as an instance
    # proxy) and under the uninstanced /World/P0; only the last is traversed.
    stage = instanced_stage(1, 1)
    settings = {"patterns": [{"match": {"type": "Cube"}, "regex": "^X"}]}
    assert [f.prim_path for f in prim_naming.check(ctx_for(stage), settings)] == ["/World/P0/Geo"]
