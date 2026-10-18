"""Run every enabled rule over every discovered file and collect findings."""

from __future__ import annotations

import os

from .model import Finding, Report, Severity, sort_findings
from .stage import AssetContext, open_stage

#: Rule ID reported when a file cannot be opened as a USD stage at all.
STAGE_OPEN_RULE = "stage-open"


def check_file(path: str) -> tuple[AssetContext, list[Finding]]:
    ctx = AssetContext(path=path, abs_path=os.path.abspath(path))
    findings: list[Finding] = []
    errors = open_stage(ctx)
    if errors:
        findings.append(
            Finding(
                rule=STAGE_OPEN_RULE,
                code="StageOpenFailed",
                severity=Severity.ERROR,
                file=path,
                message="; ".join(errors),
            )
        )
    return ctx, findings


def run(files: list[str], fail_on: str = "error") -> Report:
    findings: list[Finding] = []
    for path in files:
        _, file_findings = check_file(path)
        findings.extend(file_findings)
    return Report(files=list(files), findings=sort_findings(findings), fail_on=fail_on)
