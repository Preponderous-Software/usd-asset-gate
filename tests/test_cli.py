"""CLI behaviour: help, exit codes, missing pxr, output shapes."""

from __future__ import annotations

import json

from conftest import FAIL_DIR, PASS_DIR, REPO_ROOT, run_cli

VALID = str(PASS_DIR / "minimal.usda")
BROKEN = str(FAIL_DIR / "unparseable" / "asset.usda")


def test_help_lists_subcommands():
    proc = run_cli("--help")
    assert proc.returncode == 0
    assert "check" in proc.stdout and "rules" in proc.stdout


def test_check_valid_file_exits_0(cli):
    code, out, _ = cli("check", VALID)
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
    code, out, _ = cli("check", "--format", "json", VALID, BROKEN)
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


def test_rules_subcommand_lists_rules(cli):
    code, out, _ = cli("rules", "--format", "json")
    assert code == 0
    assert any(r["rule"] == "stage-open" for r in json.loads(out)["rules"])
