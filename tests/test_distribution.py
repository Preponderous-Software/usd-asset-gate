"""The shell layer stays free of third-party actions and leaks no token."""

from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from conftest import REPO_ROOT

USES = re.compile(r"^\s*(-\s*)?uses\s*:", re.M)


def uses_lines(path):
    return [line for line in path.read_text().splitlines() if USES.match(line)]


def test_local_only_example_has_no_uses_lines():
    assert uses_lines(REPO_ROOT / "examples" / "workflow-local-only.yml") == []


@pytest.mark.parametrize("path", ["action.yml", "ci/usd-asset-gate.sh", ".github/workflows/ci.yml"])
def test_shell_layer_has_no_uses_lines(path):
    assert uses_lines(REPO_ROOT / path) == []


def test_dogfood_workflow_uses_only_the_local_action():
    lines = uses_lines(REPO_ROOT / ".github" / "workflows" / "usd-gate.yml")
    assert [line.split(":", 1)[1].strip() for line in lines] == ["./"]


def test_action_is_composite_with_documented_inputs():
    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text())
    assert action["runs"]["using"] == "composite"
    assert {"config", "usd-core-version", "paths", "comment", "fail-on"} <= set(action["inputs"])
    for step in action["runs"]["steps"]:
        assert "uses" not in step
        assert step["shell"] == "bash"
    assert {"icon", "color"} <= set(action["branding"])


@pytest.mark.parametrize("name", ["workflow-local-only.yml", "workflow-action.yml"])
def test_examples_trigger_on_pull_request_never_pull_request_target(name):
    workflow = yaml.safe_load((REPO_ROOT / "examples" / name).read_text())
    triggers = workflow.get("on", workflow.get(True))  # PyYAML reads a bare `on` as True
    assert set(triggers) == {"pull_request"}


@pytest.mark.parametrize(
    "path", ["examples/workflow-local-only.yml", "ci/usd-asset-gate.sh", ".github/workflows/usd-gate.yml"]
)
def test_token_only_travels_as_a_one_off_extraheader(path):
    text = (REPO_ROOT / path).read_text()
    assert "http.extraheader" in text
    # Only `git -c http.extraheader=...` (one command) or a read-only check; never a
    # `git config` that would persist the header in .git/config.
    assert not re.search(r"git\s+config\s+(?!--get)[^\n]*extraheader", text)
    assert "persist-credentials" not in text


def test_copy_in_script_is_valid_bash():
    proc = subprocess.run(["bash", "-n", str(REPO_ROOT / "ci" / "usd-asset-gate.sh")], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_repository_config_is_valid():
    from usd_asset_gate import config as cfg

    config = cfg.load(str(REPO_ROOT / ".usd-asset-gate.yml"))
    assert config.include == ["tests/fixtures/pass/**"]
