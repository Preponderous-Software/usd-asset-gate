"""CLI behaviour: help, exit codes, missing pxr, output shapes."""

from __future__ import annotations

import json

from conftest import DEFAULT_CONFIG, FAIL_DIR, PASS_DIR, REPO_ROOT, run_cli

DEFAULTS = ("--config", str(DEFAULT_CONFIG))

VALID = str(PASS_DIR / "minimal.usda")
BROKEN = str(FAIL_DIR / "unparseable" / "asset.usda")


def test_help_lists_subcommands():
    proc = run_cli("--help")
    assert proc.returncode == 0
    assert "check" in proc.stdout and "rules" in proc.stdout


def test_check_valid_file_exits_0(cli):
    code, out, _ = cli("check", *DEFAULTS, VALID)
    assert code == 0
    assert out.strip().endswith("(fail_on: error)")
    assert out.startswith("PASS: 1 file checked")


def test_check_broken_file_exits_1(cli):
    code, out, _ = cli("check", BROKEN)
    assert code == 1
    assert "stage-open" in out and "StageOpenFailed" in out
    assert "FAIL: 1 file checked, 1 error(s)" in out


def test_fail_on_never_exits_0_even_with_errors(cli):
    code, _, _ = cli("check", "--fail-on", "never", BROKEN)
    assert code == 0


def test_json_output_has_documented_top_level_shape(cli):
    code, out, _ = cli("check", *DEFAULTS, "--format", "json", VALID, BROKEN)
    assert code == 1
    data = json.loads(out)
    assert set(data) == {"version", "tool", "files", "findings", "summary"}
    assert data["version"] == 1
    assert data["tool"]["name"] == "usd-asset-gate"
    assert [f["path"] for f in data["files"]] == sorted(
        [
            "tests/fixtures/fail/unparseable/asset.usda",
            "tests/fixtures/pass/minimal.usda",
        ]
    )
    assert data["summary"] == {
        "files": 2,
        "error": 1,
        "warning": 0,
        "info": 0,
        "fail_on": "error",
        "exit_code": 1,
        "passed": False,
    }
    (finding,) = data["findings"]
    assert set(finding) == {
        "rule",
        "code",
        "severity",
        "file",
        "message",
        "prim_path",
        "layer",
        "line",
        "fixer_available",
    }


def test_directory_is_expanded_to_usd_files(cli):
    code, out, _ = cli("check", "--format", "json", str(PASS_DIR))
    data = json.loads(out)
    assert data["summary"]["files"] >= 1
    assert all(f["path"].startswith("tests/fixtures/pass/") for f in data["files"])


def test_missing_path_exits_2_naming_it(cli):
    code, _, err = cli("check", "does/not/exist.usda")
    assert code == 2
    assert "does/not/exist.usda" in err


def test_no_paths_exits_2(cli):
    code, _, err = cli("check")
    assert code == 2
    assert "no paths" in err


def test_unknown_option_exits_2():
    proc = run_cli("check", "--no-such-flag")
    assert proc.returncode == 2


def test_missing_pxr_exits_2_naming_usd_core(tmp_path):
    # Shadow the real package with one that fails to import, as if usd-core were absent.
    fake = tmp_path / "pxr"
    fake.mkdir()
    (fake / "__init__.py").write_text("raise ImportError('No module named pxr')\n")
    proc = run_cli("check", VALID, env={"PYTHONPATH": f"{tmp_path}:{REPO_ROOT}"})
    assert proc.returncode == 2
    assert "usd-core" in proc.stderr
    assert "pip install usd-core" in proc.stderr


def test_help_works_without_pxr(tmp_path):
    fake = tmp_path / "pxr"
    fake.mkdir()
    (fake / "__init__.py").write_text("raise ImportError('No module named pxr')\n")
    proc = run_cli("--help", env={"PYTHONPATH": f"{tmp_path}:{REPO_ROOT}"})
    assert proc.returncode == 0


def test_too_old_usd_core_exits_2_naming_the_floor(monkeypatch, cli):
    from pxr import Usd

    monkeypatch.setattr(Usd, "GetVersion", lambda: (0, 24, 11))
    code, out, err = cli("check", *DEFAULTS, VALID)
    assert code == 2
    assert out == ""
    assert "usd-core 24.11 is too old" in err
    assert "usd-core >= 25.8" in err


def test_verbose_prints_openusd_diagnostics_to_stderr(monkeypatch, cli):
    from usd_asset_gate import runner as runner_mod
    from usd_asset_gate.model import Report

    report = Report(files=["a.usda"], findings=[], diagnostics={"a.usda": ["Warning: one", "Warning: two"]})
    monkeypatch.setattr(runner_mod.Runner, "run", lambda self, files, fail_on=None: report)
    code, _, err = cli("check", *DEFAULTS, VALID)
    assert code == 0
    assert err == ""
    code, _, err = cli("check", *DEFAULTS, "--verbose", VALID)
    assert code == 0
    assert err == "a.usda: openusd: Warning: one\na.usda: openusd: Warning: two\n"


def test_max_annotations_caps_each_severity(cli):
    case = FAIL_DIR / "bad_prim_names"
    args = ("check", "--config", str(case / ".usd-asset-gate.yml"), "--format", "github", str(case))
    code, out, _ = cli(*args)
    assert code == 1
    assert sum(line.startswith("::error ") for line in out.splitlines()) == 2
    assert "not annotated" not in out
    code, out, _ = cli(*args, "--max-annotations", "1")
    assert code == 1
    lines = out.splitlines()
    assert sum(line.startswith("::error ") for line in lines) == 1
    assert lines[1] == (
        "::notice title=usd-asset-gate::1 more finding(s) are not annotated; "
        "see the job summary and the log for the full list."
    )


def test_comment_with_unreadable_previews_exits_2(tmp_path, cli):
    from usd_asset_gate.model import Report
    from usd_asset_gate.report import json as json_report

    report = tmp_path / "report.json"
    report.write_text(json_report.render(Report(files=[], findings=[])))
    previews = tmp_path / "previews.json"
    previews.write_text('{"status": "ok"}')
    code, out, err = cli("comment", "--from", str(report), "--previews", str(previews))
    assert code == 2
    assert out == ""
    assert f"cannot read previews {previews}" in err
    assert "no 'files'" in err


def test_rules_subcommand_lists_rules(cli):
    code, out, _ = cli("rules", "--format", "json")
    assert code == 0
    assert any(r["rule"] == "stage-open" for r in json.loads(out)["rules"])


def test_rules_text_header_without_a_config_file(tmp_path, cli):
    code, out, _ = cli("rules", "--path", "assets/a.usda", cwd=tmp_path)
    assert code == 0
    assert out.startswith("config: (defaults)\npath: assets/a.usda\nfail_on: error\n\n")


def test_rules_text_shows_params_and_claiming_aliases(tmp_path, cli):
    config = tmp_path / "gate.yml"
    config.write_text("version: 1\nrules:\n  prim-budget: {max_prims: 100}\n")
    code, out, _ = cli("rules", "--config", str(config))
    assert code == 0
    assert out.startswith(f"config: {config}\nfail_on: error\n\n")
    lines = out.splitlines()
    assert 'prim-budget  [house, enabled, severity warning]  {"max_prims": 100}' in lines
    (composition,) = [line for line in lines if line.startswith("upstream:usdValidation:CompositionErrorTest  [")]
    assert composition.endswith("  (claimed by unresolved-references, unresolved-sublayers when enabled)")


def test_tf_error_messages_keep_spaces_inside_paths():
    from usd_asset_gate.stage import tf_error_messages

    exc = Exception(
        "\n\tError in 'A' at line 1 in file a.cpp : 'Failed to open layer @/tmp/my  assets/x.usda@'"
        "\n\tError in 'B' at line 2 in file b.h : '/tmp/my  assets/x.usda:4:1: Expected }\n'"
    )
    assert tf_error_messages(exc) == [
        "Failed to open layer @/tmp/my  assets/x.usda@",
        "/tmp/my  assets/x.usda:4:1: Expected }",
    ]


def test_diagnostics_drop_per_run_stage_addresses():
    from usd_asset_gate.stage import normalize_diagnostic

    a = "Warning: Could not open asset @x.usda@ (instantiating stage on stage @a.usda@ <0x361c760>)"
    b = "Warning: Could not open asset @x.usda@ (instantiating stage on stage @a.usda@ <0x7f00aa12>)"
    assert normalize_diagnostic(a) == normalize_diagnostic(b)
    assert normalize_diagnostic(a).endswith("(instantiating stage on stage @a.usda@)")
