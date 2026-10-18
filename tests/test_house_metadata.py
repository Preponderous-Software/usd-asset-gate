"""House rules: stage-metadata values, default-prim kind, file-size."""

from __future__ import annotations

import pytest
from pxr import Usd, UsdGeom

from conftest import PASS_DIR
from usd_asset_gate import config as cfg
from usd_asset_gate import runner as runner_mod
from usd_asset_gate.model import Severity
from usd_asset_gate.rules.house import default_prim, file_size, stage_metadata
from usd_asset_gate.runner import Runner
from usd_asset_gate.stage import AssetContext


def ctx_for(stage) -> AssetContext:
    return AssetContext(path="mem.usda", abs_path="mem.usda", stage=stage)


def stage_with(mpu=None, up=None, kind=None, default=True):
    stage = Usd.Stage.CreateInMemory()
    prim = stage.DefinePrim("/Asset", "Xform")
    if default:
        stage.SetDefaultPrim(prim)
    if mpu is not None:
        UsdGeom.SetStageMetersPerUnit(stage, mpu)
    if up is not None:
        UsdGeom.SetStageUpAxis(stage, up)
    if kind is not None:
        Usd.ModelAPI(prim).SetKind(kind)
    return stage


# --- stage-metadata -------------------------------------------------------------


def test_meters_per_unit_mismatch_fails_and_match_passes():
    settings = {"metersPerUnit": 0.01}
    (f,) = stage_metadata.check(ctx_for(stage_with(mpu=1.0)), settings)
    assert (f.rule, f.code, f.severity) == ("stage-metadata", "MetersPerUnitMismatch", Severity.ERROR)
    assert f.file == "mem.usda" and f.prim_path is None
    assert "0.01" in f.message and "is 1" in f.message
    assert stage_metadata.check(ctx_for(stage_with(mpu=0.01)), settings) == []


def test_meters_per_unit_tolerance_boundaries():
    settings = {"metersPerUnit": 0.01, "tolerance": 0.001}
    assert stage_metadata.check(ctx_for(stage_with(mpu=0.0109)), settings) == []
    assert stage_metadata.check(ctx_for(stage_with(mpu=0.0111)), settings) != []
    # Default tolerance is tight: 1e-9.
    assert stage_metadata.check(ctx_for(stage_with(mpu=0.0100001)), {"metersPerUnit": 0.01}) != []


def test_up_axis_mismatch():
    (f,) = stage_metadata.check(ctx_for(stage_with(up=UsdGeom.Tokens.z)), {"upAxis": "Y"})
    assert f.code == "UpAxisMismatch"
    assert stage_metadata.check(ctx_for(stage_with(up=UsdGeom.Tokens.y)), {"upAxis": "Y"}) == []


def test_unauthored_values_are_left_to_the_presence_check():
    # Missing metadata is reported by the upstream presence check, not twice.
    assert stage_metadata.check(ctx_for(stage_with()), {"metersPerUnit": 0.01, "upAxis": "Y"}) == []


def test_nothing_configured_checks_nothing():
    assert stage_metadata.check(ctx_for(stage_with(mpu=5.0, up=UsdGeom.Tokens.z)), {}) == []


def test_severity_override_applies():
    (f,) = stage_metadata.check(
        ctx_for(stage_with(mpu=1.0)), {"metersPerUnit": 0.01, "severity": "warning"}
    )
    assert f.severity is Severity.WARNING


# --- default-prim kind ------------------------------------------------------------


def test_kind_mismatch_carries_prim_path():
    (f,) = default_prim.check(ctx_for(stage_with(kind="assembly")), {"kind": "component"})
    assert (f.rule, f.code, f.prim_path) == ("default-prim", "DefaultPrimKindMismatch", "/Asset")
    assert "'assembly'" in f.message


def test_kind_match_and_derived_kind_pass():
    assert default_prim.check(ctx_for(stage_with(kind="component")), {"kind": "component"}) == []
    # assembly IsA group IsA model, so a "model" requirement accepts it.
    assert default_prim.check(ctx_for(stage_with(kind="assembly")), {"kind": "model"}) == []


def test_missing_kind_fails():
    (f,) = default_prim.check(ctx_for(stage_with()), {"kind": "component"})
    assert "no kind" in f.message


def test_missing_default_prim_is_not_reported_twice():
    assert default_prim.check(ctx_for(stage_with(default=False)), {"kind": "component"}) == []


# --- file-size --------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (1024, 1024),
        ("100B", 100),
        ("5KB", 5000),
        ("50MB", 50_000_000),
        ("2GiB", 2 * 1024**3),
        ("1.5 MiB", int(1.5 * 1024**2)),
        ("3kb", 3000),
    ],
)
def test_parse_size(value, expected):
    assert file_size.parse_size(value) == expected


def test_parse_size_rejects_garbage():
    with pytest.raises(ValueError):
        file_size.parse_size("lots")


def test_limit_per_extension():
    limits = {"default": "1MB", ".usdz": "2MB"}
    assert file_size.limit_for("a/b.usdz", limits) == 2_000_000
    assert file_size.limit_for("a/b.USDA", limits) == 1_000_000
    assert file_size.limit_for("a/b.usda", {".usdz": "2MB"}) is None
    assert file_size.limit_for("a/b.usda", "10KB") == 10_000
    assert file_size.limit_for("a/b.usda", None) is None


def test_oversize_file_is_reported_without_opening_the_stage(tmp_path, monkeypatch):
    big = tmp_path / "big.usda"
    big.write_text((PASS_DIR / "minimal.usda").read_text() + "\n# padding\n" + "#" * 2000 + "\n")
    small = tmp_path / "small.usda"
    small.write_text((PASS_DIR / "minimal.usda").read_text())
    monkeypatch.chdir(tmp_path)

    opened = []
    real_open = runner_mod.open_stage
    monkeypatch.setattr(runner_mod, "open_stage", lambda ctx: opened.append(ctx.path) or real_open(ctx))

    config = cfg.parse({"rules": {"file-size": {"max_bytes": {"default": "1KB"}}}})
    report = Runner(config).run(["big.usda", "small.usda"])
    assert [(f.file, f.rule, f.code) for f in report.findings] == [
        ("big.usda", "file-size", "FileTooLarge")
    ]
    assert "1.0 KB" in report.findings[0].message
    assert opened == ["small.usda"]  # the oversize file was never opened


def test_size_exactly_at_limit_passes(tmp_path):
    f = tmp_path / "x.usda"
    f.write_bytes(b"#usda 1.0\n" + b"#" * 90)  # exactly 100 bytes
    ctx = AssetContext(path="x.usda", abs_path=str(f))
    assert file_size.check_before_open(ctx, {"max_bytes": 100}) == []
    assert file_size.check_before_open(ctx, {"max_bytes": 99}) != []


@pytest.mark.parametrize(
    "rules, key",
    [
        ({"file-size": {"max_bytes": "lots"}}, "rules.file-size.max_bytes"),
        ({"file-size": {"max_bytes": {".txt": "1MB"}}}, "rules.file-size.max_bytes..txt: unknown key"),
        ({"file-size": {"max_bytes": -1}}, "rules.file-size.max_bytes"),
        ({"stage-metadata": {"metersPerUnit": 0}}, "rules.stage-metadata.metersPerUnit: must be > 0"),
        ({"stage-metadata": {"upAxis": "X"}}, "rules.stage-metadata.upAxis"),
        ({"stage-metadata": {"metersPerUnit": "cm"}}, "rules.stage-metadata.metersPerUnit: expected number"),
        ({"default-prim": {"kind": 3}}, "rules.default-prim.kind: expected string"),
    ],
)
def test_invalid_house_settings_name_the_key(rules, key):
    with pytest.raises(cfg.ConfigError) as exc:
        cfg.parse({"rules": rules})
    assert key in str(exc.value)

