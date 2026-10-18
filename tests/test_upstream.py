"""Upstream adapter over pxr.UsdValidation."""

from __future__ import annotations

import pytest
from pxr import Sdf, Usd, UsdValidation

from conftest import FAIL_DIR
from usd_asset_gate.model import Severity
from usd_asset_gate.rules import upstream
from usd_asset_gate.runner import check_file, run
from usd_asset_gate.stage import AssetContext

HAS_FIXERS_API = hasattr(UsdValidation.ValidationError, "GetFixers")


def bare_stage_ctx(path: str = "mem.usda") -> AssetContext:
    """An in-memory stage with one prim and no stage metadata at all."""
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim("/Root", "Xform")
    return AssetContext(path=path, abs_path=path, stage=stage)


def test_registry_is_discovered_at_runtime():
    infos = upstream.all_validators()
    names = [v.name for v in infos]
    assert names == sorted(names)
    # usd-core 25.8 registers 24 validators, 26.8 registers 28 (checked 2026-10-17).
    assert len(names) >= 24
    for expected in (
        "usdValidation:StageMetadataChecker",
        "usdValidation:CompositionErrorTest",
        "usdGeomValidators:StageMetadataChecker",
        "usdUtilsValidators:MissingReferenceValidator",
    ):
        assert expected in names
    assert all(v.rule == "upstream:" + v.name for v in infos)


def test_select_all_by_default():
    infos = upstream.all_validators()
    assert upstream.select_validators(infos) == [v.name for v in infos]


def test_select_by_keyword():
    chosen = upstream.select_validators(upstream.all_validators(), keywords=["UsdGeomValidators"])
    assert chosen
    assert all(name.startswith("usdGeomValidators:") for name in chosen)


def test_select_by_name_glob_and_disable():
    infos = upstream.all_validators()
    chosen = upstream.select_validators(
        infos,
        validators=["usdUtilsValidators:*", "usdValidation:StageMetadataChecker"],
        disable=["usdUtilsValidators:Usdz*"],
    )
    assert "usdValidation:StageMetadataChecker" in chosen
    assert "usdUtilsValidators:MissingReferenceValidator" in chosen
    assert "usdUtilsValidators:UsdzPackageValidator" not in chosen
    assert all(
        n.startswith("usdUtilsValidators:") or n == "usdValidation:StageMetadataChecker"
        for n in chosen
    )


def test_disable_everything_by_glob():
    assert upstream.select_validators(upstream.all_validators(), disable=["*"]) == []


def test_keyword_selection_limits_findings():
    ctx = bare_stage_ctx()
    everything = upstream.check(ctx, [v.name for v in upstream.all_validators()])
    assert {f.code for f in everything} >= {
        "MissingDefaultPrim",
        "MissingUpAxisMetadata",
        "MissingMetersPerUnitMetadata",
    }
    geom_only = upstream.select_validators(
        upstream.all_validators(), keywords=["UsdGeomValidators"]
    )
    limited = upstream.check(bare_stage_ctx(), geom_only)
    assert sorted(f.code for f in limited) == [
        "MissingMetersPerUnitMetadata",
        "MissingUpAxisMetadata",
    ]
    assert {f.rule for f in limited} == {"upstream:usdGeomValidators:StageMetadataChecker"}


def test_error_maps_to_finding_fields():
    ctx, findings = check_file(str(FAIL_DIR / "missing_reference" / "asset.usda"))
    by_code = {f.code: f for f in findings}
    comp = by_code["CompositionError"]
    assert comp.rule == "upstream:usdValidation:CompositionErrorTest"
    assert comp.severity is Severity.ERROR
    assert comp.prim_path == "/Crate"
    assert comp.layer is None  # the root layer is not repeated as "layer"
    assert comp.file.endswith("missing_reference/asset.usda")
    # Absolute paths in OpenUSD messages are made relative to the working directory.
    assert "tests/fixtures/fail/missing_reference/textures/crate_base.usda" in comp.message
    assert "/root/" not in comp.message and not comp.message.startswith("/")
    dep = by_code["UnresolvableDependency"]
    assert dep.prim_path is None


def test_stage_level_site_has_no_prim_path():
    findings = upstream.check(bare_stage_ctx(), ["usdValidation:StageMetadataChecker"])
    (finding,) = findings
    assert finding.code == "MissingDefaultPrim"
    assert finding.prim_path is None


def test_fixer_presence_is_reported_not_applied():
    path = FAIL_DIR / "missing_material_binding_api" / "asset.usda"
    before = path.read_bytes()
    ctx, findings = check_file(str(path))
    (finding,) = findings
    assert finding.code == "MissingMaterialBindingAPI"
    assert finding.prim_path == "/Root/Geo"
    assert finding.fixer_available is HAS_FIXERS_API
    # Nothing was fixed: the stage still lacks the API and the file is untouched.
    assert not ctx.stage.GetPrimAtPath("/Root/Geo").HasAPI("MaterialBindingAPI")
    assert path.read_bytes() == before


def test_composition_warnings_are_captured_not_just_printed(capfd):
    report = run([str(FAIL_DIR / "missing_reference" / "asset.usda")])
    out, err = capfd.readouterr()
    assert "Could not open asset" not in err
    (lines,) = report.diagnostics.values()
    assert any("Could not open asset" in line for line in lines)


def test_unknown_names_are_ignored_by_run_validators():
    assert upstream.run_validators(bare_stage_ctx(), []) == []


def test_validation_run_failure_is_retried_then_reported(monkeypatch):
    from pxr import Tf

    calls = []

    class Exploding:
        def Validate(self, stage):
            calls.append(1)
            raise Tf.ErrorException("Error in 'X' at line 1 in file y.cpp : 'boom'")

    ctx = bare_stage_ctx()
    with pytest.raises(upstream.ValidationRunError, match="boom"):
        upstream._validate(ctx, Exploding())
    assert len(calls) == upstream.VALIDATE_ATTEMPTS


def test_validation_run_failure_becomes_a_finding(monkeypatch):
    def fail(ctx, names):
        raise upstream.ValidationRunError("boom")

    monkeypatch.setattr(upstream, "run_validators", fail)
    (finding,) = upstream.check(bare_stage_ctx(), ["x"])
    assert finding.rule == "upstream"
    assert finding.code == "ValidationRunFailed"


def test_transient_validation_failure_recovers():
    from pxr import Tf, UsdValidation

    real = UsdValidation.ValidationContext(
        UsdValidation.ValidationRegistry().GetOrLoadValidatorsByName(
            ["usdValidation:StageMetadataChecker"]
        )
    )
    state = {"n": 0}

    class Flaky:
        def Validate(self, stage):
            state["n"] += 1
            if state["n"] == 1:
                raise Tf.ErrorException("Error in 'X' at line 1 in file y.cpp : 'race'")
            return real.Validate(stage)

    errors = upstream._validate(bare_stage_ctx(), Flaky())
    assert [e.GetName() for e in errors] == ["MissingDefaultPrim"]


class _FakeLayer:
    def __init__(self, identifier):
        self.identifier = identifier


class _FakeSite:
    def __init__(self, layer=None, prim=None):
        self._layer, self._prim = layer, prim

    def IsProperty(self):
        return False

    def IsPrim(self):
        return self._prim is not None

    def GetPrim(self):
        return self._prim

    def GetLayer(self):
        return self._layer


class _FakeStage:
    def __init__(self, root):
        self._root = _FakeLayer(root)

    def GetRootLayer(self):
        return self._root


class _FakeError:
    def __init__(self, sites):
        self._sites = sites

    def GetSites(self):
        return self._sites


def test_layer_site_outside_root_is_reported_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = str(tmp_path / "root.usda")
    ctx = AssetContext(path="root.usda", abs_path=root, stage=_FakeStage(root))
    error = _FakeError([_FakeSite(layer=_FakeLayer(root)), _FakeSite(layer=_FakeLayer(str(tmp_path / "sub" / "a.usda")))])
    assert upstream._sites(ctx, error) == (None, "sub/a.usda")
    # The root layer itself is never repeated as "layer".
    assert upstream._sites(ctx, _FakeError([_FakeSite(layer=_FakeLayer(root))])) == (None, None)
