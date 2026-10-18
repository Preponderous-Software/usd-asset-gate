"""GitHub Actions workflow commands: ``::error file=…,line=…,title=…::message``.

Escaping follows ``@actions/toolkit`` (``packages/core/src/command.ts``): message
data escapes ``%``, ``\\r`` and ``\\n``; property values additionally escape ``:``
and ``,``.

GitHub shows only a limited number of annotations per step, and the limit is not
stated on the workflow-commands documentation page (checked 2026-10-18). The
reporter therefore emits the most severe findings first, at most
``max_per_severity`` of each severity (default 10), and then one notice saying
how many more are only in the job summary and the log.
"""

from __future__ import annotations

import secrets

from ..model import Finding, Report, Severity

DEFAULT_MAX_PER_SEVERITY = 10

_COMMAND = {Severity.ERROR: "error", Severity.WARNING: "warning", Severity.INFO: "notice"}


def escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def annotation(finding: Finding) -> str:
    props = [f"file={escape_property(finding.file)}"]
    if finding.line is not None:
        props.append(f"line={finding.line}")
    props.append(f"title={escape_property('usd-asset-gate: ' + finding.rule)}")
    message = finding.message
    if finding.prim_path:
        message = f"<{finding.prim_path}> {message}"
    message += f" [{finding.code}]"
    if finding.fixer_available:
        message += " (an OpenUSD fixer exists; not applied)"
    return f"::{_COMMAND[finding.severity]} {','.join(props)}::{escape_data(message)}"


def render(report: Report, max_per_severity: int = DEFAULT_MAX_PER_SEVERITY) -> str:
    ordered = sorted(report.findings, key=lambda f: (-f.severity.rank, f.sort_key()))
    emitted = {s: 0 for s in Severity}
    lines = []
    skipped = 0
    for f in ordered:
        if emitted[f.severity] >= max_per_severity:
            skipped += 1
            continue
        emitted[f.severity] += 1
        lines.append(annotation(f))
    if skipped:
        lines.append(
            "::notice title=usd-asset-gate::"
            + escape_data(
                f"{skipped} more finding(s) are not annotated; see the job summary and the "
                "log for the full list."
            )
        )
    return "\n".join(lines) + ("\n" if lines else "")


def guarded(text: str, token: str | None = None) -> str:
    """Wrap human-readable output so the runner does not execute commands inside it.

    File names and messages come from the pull request. Between
    ``::stop-commands::<token>`` and ``::<token>::`` GitHub ignores workflow
    commands, so a file name containing a newline and ``::error::...`` (or
    ``::stop-commands::``) cannot forge annotations. The token is random per run,
    so the text cannot end the guard early.
    """
    token = token or secrets.token_hex(16)
    if not text.endswith("\n"):
        text += "\n"
    return f"::stop-commands::{token}\n{text}::{token}::\n"
