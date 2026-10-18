"""Human-readable text output: ``path:prim: severity rule: message``."""

from __future__ import annotations

import os
from typing import TextIO

from ..model import Finding, Report, Severity

_COLOURS = {
    Severity.ERROR: "\033[31m",
    Severity.WARNING: "\033[33m",
    Severity.INFO: "\033[36m",
}
_RESET = "\033[0m"


def use_colour(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def format_finding(finding: Finding, colour: bool = False) -> str:
    location = finding.file
    if finding.line is not None:
        location += f":{finding.line}"
    if finding.prim_path:
        location += f":{finding.prim_path}"
    severity = finding.severity.value
    if colour:
        severity = f"{_COLOURS[finding.severity]}{severity}{_RESET}"
    text = f"{location}: {severity} {finding.rule}: {finding.message} [{finding.code}]"
    if finding.layer and finding.layer != finding.file:
        text += f" (in layer {finding.layer})"
    if finding.fixer_available:
        text += " (an OpenUSD fixer exists; not applied)"
    return text


def summary_line(report: Report) -> str:
    counts = report.counts()
    n = len(report.files)
    verdict = "FAIL" if report.exit_code else "PASS"
    return (
        f"{verdict}: {n} file{'s' if n != 1 else ''} checked, "
        f"{counts['error']} error(s), {counts['warning']} warning(s), "
        f"{counts['info']} info (fail_on: {report.fail_on})"
    )


def render(report: Report, colour: bool = False) -> str:
    lines = [format_finding(f, colour) for f in report.findings]
    lines.append(summary_line(report))
    return "\n".join(lines) + "\n"
