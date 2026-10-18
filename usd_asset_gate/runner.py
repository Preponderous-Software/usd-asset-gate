"""Run every enabled rule over every discovered file and collect findings."""

from __future__ import annotations

import os
from typing import Iterable

from .model import Finding, Report, Severity, sort_findings
from .rules import upstream
from .stage import AssetContext, normalize_diagnostic, open_stage

#: Rule ID reported when a file cannot be opened as a USD stage at all.
STAGE_OPEN_RULE = "stage-open"


def check_file(
    path: str, upstream_names: Iterable[str] | None = None
) -> tuple[AssetContext, list[Finding]]:
    """Check one file. ``upstream_names`` defaults to every registered validator."""
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
    if upstream_names is None:
        upstream_names = [v.name for v in upstream.all_validators()]
    findings.extend(upstream.check(ctx, upstream_names))
    return ctx, findings


def run(
    files: list[str],
    fail_on: str = "error",
    upstream_names: Iterable[str] | None = None,
) -> Report:
    if upstream_names is not None:
        upstream_names = list(upstream_names)
    findings: list[Finding] = []
    diagnostics: dict[str, list[str]] = {}
    for path in files:
        ctx, file_findings = check_file(path, upstream_names)
        findings.extend(file_findings)
        if ctx.diagnostics:
            diagnostics[path] = [
                normalize_diagnostic(ctx.relativize(line)) for line in ctx.diagnostics
            ]
    return Report(
        files=list(files),
        findings=sort_findings(findings),
        fail_on=fail_on,
        diagnostics=diagnostics,
    )
