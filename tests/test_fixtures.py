"""The fixture corpus contract.

Every file under ``tests/fixtures/pass/`` exits 0 with the default config. Every
directory under ``tests/fixtures/fail/`` holds one defect and an ``expected.json``
whose ``findings`` list the ``(rule, code)`` pairs reported for that directory —
no more, no fewer (compared as a multiset).
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from conftest import PASS_DIR, fail_cases, load_expected, run_cli

PASS_FILES = sorted(
    p for p in PASS_DIR.rglob("*") if p.suffix.lower() in {".usd", ".usda", ".usdc", ".usdz"}
)


def test_corpus_is_not_empty():
    assert PASS_FILES, "no pass fixtures found"
    assert fail_cases(), "no fail fixtures found"


@pytest.mark.parametrize("path", PASS_FILES, ids=lambda p: str(p.relative_to(PASS_DIR)))
def test_pass_fixture_exits_0(path):
    proc = run_cli("check", "--format", "json", str(path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["findings"] == []


@pytest.mark.parametrize("case", fail_cases(), ids=lambda p: p.name)
def test_fail_fixture_reports_exactly_expected(case):
    expected = load_expected(case)
    proc = run_cli("check", "--format", "json", str(case))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    got = Counter((f["rule"], f["code"]) for f in report["findings"])
    want = Counter((f["rule"], f["code"]) for f in expected["findings"])
    assert got == want
    for finding in report["findings"]:
        assert finding["file"].startswith(f"tests/fixtures/fail/{case.name}/")
