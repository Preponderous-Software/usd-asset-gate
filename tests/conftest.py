"""Shared test helpers."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
PASS_DIR = FIXTURES / "pass"
FAIL_DIR = FIXTURES / "fail"


def pytest_configure(config):
    """Generate the binary fixtures before tests are collected."""
    sys.path.insert(0, str(FIXTURES))
    try:
        import build
    finally:
        sys.path.remove(str(FIXTURES))
    build.build()


def fail_cases() -> list[pathlib.Path]:
    return sorted(p for p in FAIL_DIR.iterdir() if (p / "expected.json").is_file())


def load_expected(case: pathlib.Path) -> dict:
    return json.loads((case / "expected.json").read_text(encoding="utf-8"))


def run_cli(*args: str, cwd: os.PathLike | str | None = None, env: dict | None = None):
    """Run ``python -m usd_asset_gate`` in a subprocess; return CompletedProcess."""
    full_env = dict(os.environ)
    full_env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + full_env.get("PYTHONPATH", "")
    full_env.pop("GITHUB_ACTIONS", None)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "usd_asset_gate", *args],
        cwd=cwd or REPO_ROOT,
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def cli(capsys, monkeypatch):
    """Run the CLI in-process; return (exit_code, stdout, stderr)."""
    from usd_asset_gate.cli import main

    def _run(*args: str, cwd: os.PathLike | str | None = None):
        if cwd is not None:
            monkeypatch.chdir(cwd)
        code = main(list(args))
        out, err = capsys.readouterr()
        return code, out, err

    return _run
