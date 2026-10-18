"""Config loading, schema validation, overrides, fail_on and the rules command."""

from __future__ import annotations

import json
import pathlib

import pytest

from conftest import FAIL_DIR, PASS_DIR, REPO_ROOT, run_cli
from usd_asset_gate import config as cfg
from usd_asset_gate.discovery import filter_paths, glob_to_regex
from usd_asset_gate.runner import Runner

NO_DEFAULT_PRIM = str(FAIL_DIR / "no_default_prim" / "asset.usda")
MISSING_REF = str(FAIL_DIR / "missing_reference" / "asset.usda")


def write(tmp_path: pathlib.Path, text: str) -> str:
    path = tmp_path / ".usd-asset-gate.yml"
    path.write_text(text)
    return str(path)


# --- schema validation: exit 2 naming the key ---------------------------------


@pytest.mark.parametrize(
    "text, key",
    [
        ("rules:\n  prim-budgt: {}\n", "rules.prim-budgt: unknown rule"),
        ("rules:\n  default-prim:\n    severity: fatal\n", "rules.default-prim.severity"),
        ("rules:\n  default-prim:\n    enabled: 'yes'\n", "rules.default-prim.enabled: expected boolean"),
        ("include: 'assets/**'\n", "include: expected array, got string"),
        ("fail_on: sometimes\n", "fail_on"),
        ("colour: red\n", "colour: unknown key"),
        ("version: 2\n", "version"),
        ("fail_on: 2026-10-18\n", "fail_on: '2026-10-18' is not one of"),
        ("upstream:\n  keywords: UsdGeomValidators\n", "upstream.keywords: expected array"),
        ("overrides:\n  - rules: {}\n", "overrides[0].paths: required key is missing"),
        ("overrides:\n  - paths: ['a/**']\n    rules:\n      nope: {}\n", "overrides[0].rules.nope: unknown rule"),
        ("rules:\n  upstream:usdFake:Nope: {severity: warning}\n", "rules.upstream:usdFake:Nope: unknown rule"),
        ("upstream:\n  disable: ['usdGeomValidators:NoSuchValidator']\n", "upstream.disable[0]"),
        ("- just\n- a list\n", "(top level): expected object, got array"),
    ],
)
def test_invalid_config_exits_2_naming_the_key(tmp_path, cli, text, key):
    config = write(tmp_path, text)
    code, out, err = cli("check", "--config", config, str(PASS_DIR / "minimal.usda"))
    assert code == 2
    assert key in err
    assert out == ""  # nothing was checked


def test_invalid_yaml_exits_2(tmp_path, cli):
    config = write(tmp_path, "rules: [unclosed\n")
    code, _, err = cli("check", "--config", config, str(PASS_DIR / "minimal.usda"))
    assert code == 2
    assert "invalid YAML" in err


def test_missing_config_file_exits_2(cli):
    code, _, err = cli("check", "--config", "nope.yml", str(PASS_DIR / "minimal.usda"))
    assert code == 2
    assert "nope.yml" in err


def test_config_error_stops_before_any_asset_is_opened(tmp_path, monkeypatch):
    from usd_asset_gate import cli as cli_mod
    from usd_asset_gate import runner

    opened = []
    real_open = runner.open_stage
    monkeypatch.setattr(runner, "open_stage", lambda ctx: opened.append(ctx) or real_open(ctx))
    # The stub is live: a valid config does open the asset.
    assert cli_mod.main(["check", NO_DEFAULT_PRIM]) == 1
    assert len(opened) == 1
    opened.clear()
    config = write(tmp_path, "rules:\n  bogus: {}\n")
    assert cli_mod.main(["check", "--config", config, NO_DEFAULT_PRIM]) == 2
    assert opened == []


def test_empty_file_means_defaults(tmp_path):
    config = cfg.load(write(tmp_path, ""))
    assert config.fail_on == "error"
    assert config.include is None
    assert config.rule_settings()["default-prim"] == {"enabled": True, "severity": "error"}


def test_default_file_is_picked_up_from_working_directory(tmp_path, monkeypatch):
    write(tmp_path, "fail_on: never\n")
    monkeypatch.chdir(tmp_path)
    assert cfg.load().fail_on == "never"


def test_no_file_means_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cfg.load().source is None


def test_published_schema_matches_python_schema():
    published = json.loads((REPO_ROOT / "schema" / "usd-asset-gate.schema.json").read_text())
    assert published == cfg.build_schema(), (
        "regenerate with: python -m usd_asset_gate.config > schema/usd-asset-gate.schema.json"
    )


def test_architecture_example_is_valid_for_the_rules_it_names():
    data = {
        "version": 1,
        "include": ["assets/**"],
        "exclude": ["assets/legacy/**", "**/*_wip.usda"],
        "fail_on": "error",
        "upstream": {
            "enabled": True,
            "keywords": ["UsdCoreValidators", "UsdGeomValidators"],
            "disable": ["usdPhysicsValidators:*"],
        },
        "rules": {
            "unresolved-references": {"severity": "error"},
            "unresolved-sublayers": {"severity": "error"},
        },
        "overrides": [{"paths": ["assets/environments/**"], "rules": {}}],
    }
    cfg.parse(data)


# --- overrides, severity, fail_on ---------------------------------------------


def test_override_applies_only_under_its_paths():
    config = cfg.parse(
        {
            "rules": {"default-prim": {"severity": "error"}},
            "overrides": [
                {"paths": ["assets/environments/**"], "rules": {"default-prim": {"severity": "info"}}},
                {"paths": ["assets/environments/forest/**"], "rules": {"default-prim": {"enabled": False}}},
            ],
        }
    )
    assert config.rule_settings("assets/props/crate.usda")["default-prim"] == {
        "enabled": True,
        "severity": "error",
    }
    assert config.rule_settings("assets/environments/city.usda")["default-prim"] == {
        "enabled": True,
        "severity": "info",
    }
    # Later overrides layer on top of earlier ones, in file order.
    assert config.rule_settings("assets/environments/forest/tree.usda")["default-prim"] == {
        "enabled": False,
        "severity": "info",
    }


def test_override_changes_findings_only_for_matching_files(tmp_path, monkeypatch):
    for sub in ("props", "environments"):
        (tmp_path / "assets" / sub).mkdir(parents=True)
        (tmp_path / "assets" / sub / "x.usda").write_text(
            pathlib.Path(NO_DEFAULT_PRIM).read_text()
        )
    monkeypatch.chdir(tmp_path)
    config = cfg.parse(
        {
            "overrides": [
                {"paths": ["assets/environments/**"], "rules": {"default-prim": {"severity": "warning"}}}
            ]
        }
    )
    report = Runner(config).run(["assets/environments/x.usda", "assets/props/x.usda"])
    severities = {f.file: f.severity.value for f in report.findings}
    assert severities == {"assets/environments/x.usda": "warning", "assets/props/x.usda": "error"}


def test_fail_on_warning_turns_warning_only_run_into_exit_1(tmp_path, cli):
    rules = "rules:\n  default-prim: {severity: warning}\n"
    config = write(tmp_path, rules)
    assert cli("check", "--config", config, NO_DEFAULT_PRIM)[0] == 0
    config = write(tmp_path, rules + "fail_on: warning\n")
    assert cli("check", "--config", config, NO_DEFAULT_PRIM)[0] == 1


def test_fail_on_never_always_exits_0(tmp_path, cli):
    config = write(tmp_path, "fail_on: never\n")
    code, out, _ = cli("check", "--config", config, NO_DEFAULT_PRIM, MISSING_REF)
    assert code == 0
    assert "error(s)" in out


def test_command_line_fail_on_beats_config(tmp_path, cli):
    config = write(tmp_path, "fail_on: never\n")
    assert cli("check", "--config", config, "--fail-on", "error", NO_DEFAULT_PRIM)[0] == 1


def test_disabled_alias_and_validator_report_nothing(tmp_path, cli):
    config = write(tmp_path, "rules:\n  default-prim: {enabled: false}\n")
    code, out, _ = cli("check", "--config", config, "--format", "json", NO_DEFAULT_PRIM)
    # With only the alias disabled, the upstream validator still reports under its own ID.
    assert [f["rule"] for f in json.loads(out)["findings"]] == [
        "upstream:usdValidation:StageMetadataChecker"
    ]
    config = write(
        tmp_path,
        "rules:\n  default-prim: {enabled: false}\n"
        "  upstream:usdValidation:StageMetadataChecker: {enabled: false}\n",
    )
    code, out, _ = cli("check", "--config", config, "--format", "json", NO_DEFAULT_PRIM)
    assert code == 0
    assert json.loads(out)["findings"] == []


# --- aliases and upstream selection -------------------------------------------


def test_missing_reference_is_reported_once_under_the_alias(cli):
    code, out, _ = cli("check", "--format", "json", "--config", str(REPO_ROOT / "tests/fixtures/default-config.yml"), MISSING_REF)
    findings = json.loads(out)["findings"]
    assert [(f["rule"], f["code"]) for f in findings] == [
        ("unresolved-references", "UnresolvableDependency")
    ]
    (f,) = findings
    assert f["prim_path"] == "/Crate"
    assert "textures/crate_base.usda" in f["message"]


def test_disabling_the_alias_falls_back_to_upstream_rules(tmp_path, cli):
    config = write(tmp_path, "rules:\n  unresolved-references: {enabled: false}\n")
    code, out, _ = cli("check", "--format", "json", "--config", config, MISSING_REF)
    rules = sorted(f["rule"] for f in json.loads(out)["findings"])
    assert rules == [
        "upstream:usdUtilsValidators:MissingReferenceValidator",
        "upstream:usdValidation:CompositionErrorTest",
    ]


def test_alias_runs_even_when_its_validators_are_not_selected(tmp_path, cli):
    config = write(tmp_path, "upstream:\n  enabled: false\n")
    code, out, _ = cli("check", "--format", "json", "--config", config, MISSING_REF)
    assert [f["rule"] for f in json.loads(out)["findings"]] == ["unresolved-references"]


def test_upstream_keywords_limit_findings(tmp_path, cli):
    bad = tmp_path / "bad.usda"
    bad.write_text(pathlib.Path(FAIL_DIR / "missing_material_binding_api" / "asset.usda").read_text())
    config = write(tmp_path, "upstream:\n  keywords: [UsdGeomValidators]\n")
    code, out, _ = cli("check", "--format", "json", "--config", config, str(bad))
    assert code == 0  # the shading validator was not selected
    config = write(tmp_path, "upstream:\n  keywords: [UsdShadeValidators]\n")
    code, out, _ = cli("check", "--format", "json", "--config", config, str(bad))
    assert code == 1


def test_upstream_rule_can_be_disabled_or_downgraded(tmp_path, cli):
    bad = str(FAIL_DIR / "missing_material_binding_api" / "asset.usda")
    rule = "upstream:usdShadeValidators:MaterialBindingApiAppliedValidator"
    config = write(tmp_path, f"rules:\n  {rule}: {{severity: warning}}\n")
    code, out, _ = cli("check", "--format", "json", "--config", config, bad)
    assert code == 0
    assert [f["severity"] for f in json.loads(out)["findings"]] == ["warning"]
    config = write(tmp_path, f"rules:\n  {rule}: {{enabled: false}}\n")
    code, out, _ = cli("check", "--format", "json", "--config", config, bad)
    assert json.loads(out)["findings"] == []


# --- include / exclude --------------------------------------------------------


@pytest.mark.parametrize(
    "pattern, path, expected",
    [
        ("assets/**", "assets/a/b.usda", True),
        ("assets/**", "other/a.usda", False),
        ("**/*_wip.usda", "a/b/c_wip.usda", True),
        ("**/*_wip.usda", "c_wip.usda", True),
        ("assets/*.usda", "assets/a/b.usda", False),
        ("assets/?.usda", "assets/a.usda", True),
        ("a.usda", "a.usda", True),
        ("a.usda", "b/a.usda", False),
    ],
)
def test_glob(pattern, path, expected):
    assert bool(glob_to_regex(pattern).match(path)) is expected


def test_filter_paths():
    paths = ["assets/a.usda", "assets/legacy/b.usda", "assets/c_wip.usda", "other/d.usda"]
    kept = filter_paths(paths, ["assets/**"], ["assets/legacy/**", "**/*_wip.usda"])
    assert kept == ["assets/a.usda"]
    assert filter_paths(paths, None, []) == paths


def test_include_exclude_apply_to_directories_not_explicit_files(tmp_path, cli):
    (tmp_path / "assets" / "legacy").mkdir(parents=True)
    src = (PASS_DIR / "minimal.usda").read_text()
    (tmp_path / "assets" / "a.usda").write_text(src)
    (tmp_path / "assets" / "legacy" / "old.usda").write_text(src)
    (tmp_path / "loose.usda").write_text(src)
    write(tmp_path, "include: ['assets/**']\nexclude: ['assets/legacy/**']\n")
    code, out, _ = cli("check", "--format", "json", ".", cwd=tmp_path)
    assert [f["path"] for f in json.loads(out)["files"]] == ["assets/a.usda"]
    code, out, _ = cli("check", "--format", "json", "assets/legacy/old.usda")
    assert [f["path"] for f in json.loads(out)["files"]] == ["assets/legacy/old.usda"]


# --- rules subcommand ---------------------------------------------------------


def test_rules_lists_every_rule_with_effective_settings(tmp_path, cli):
    config = write(
        tmp_path,
        "rules:\n  default-prim: {severity: warning}\n"
        "overrides:\n  - paths: ['assets/environments/**']\n    rules:\n"
        "      default-prim: {severity: info}\n",
    )
    code, out, _ = cli("rules", "--config", config, "--format", "json")
    assert code == 0
    data = json.loads(out)
    rows = {r["rule"]: r for r in data["rules"]}
    assert {"stage-open", "unresolved-references", "unresolved-sublayers", "default-prim", "stage-metadata"} <= set(rows)
    assert rows["default-prim"]["severity"] == "warning"
    assert rows["upstream:usdValidation:CompositionErrorTest"]["kind"] == "upstream"
    assert "unresolved-references" in rows["upstream:usdValidation:CompositionErrorTest"]["reported_under"]
    code, out, _ = cli(
        "rules", "--config", config, "--format", "json", "--path", "assets/environments/a.usda"
    )
    rows = {r["rule"]: r for r in json.loads(out)["rules"]}
    assert rows["default-prim"]["severity"] == "info"
    # ./relative and absolute spellings resolve to the same path the check uses.
    for spelling in ("./assets/environments/a.usda", str(pathlib.Path.cwd() / "assets/environments/a.usda")):
        code, out, _ = cli("rules", "--config", config, "--format", "json", "--path", spelling)
        data = json.loads(out)
        assert data["path"] == "assets/environments/a.usda"
        assert {r["rule"]: r for r in data["rules"]}["default-prim"]["severity"] == "info"


def test_rules_text_output(cli):
    code, out, _ = cli("rules", "--config", str(REPO_ROOT / "tests/fixtures/default-config.yml"))
    assert code == 0
    assert "default-prim  [alias, enabled, severity error]" in out
    assert "upstream:usdValidation:StageMetadataChecker" in out


def test_rules_reflects_disabled_upstream(tmp_path):
    proc = run_cli(
        "rules", "--format", "json", "--config", write(tmp_path, "upstream:\n  enabled: false\n")
    )
    rows = json.loads(proc.stdout)["rules"]
    assert not any(r["enabled"] for r in rows if r["kind"] == "upstream")
