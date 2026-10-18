"""Markdown summary for ``$GITHUB_STEP_SUMMARY`` (and the body of the PR comment).

GitHub limits each step's summary to 1 MiB ("each step is restricted to a maximum
size of 1MiB", workflow-commands docs, checked 2026-10-18). The renderer stays
under a byte budget by dropping finding rows from the end and saying how many
were left out.
"""

from __future__ import annotations

from ..model import Report

#: Default byte budget for one summary: under 1 MiB with room for other steps' text.
DEFAULT_MAX_BYTES = 1024 * 1024 - 64 * 1024

_ICON = {"error": "🔴", "warning": "🟡", "info": "🔵"}


def cell(text: str) -> str:
    """Make text safe inside a Markdown table cell."""
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def code(text: str) -> str:
    text = text.replace("|", "\\|").replace("\n", " ")
    fence = "``" if "`" in text else "`"
    pad = " " if fence == "``" else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def headline(report: Report) -> str:
    counts = report.counts()
    n = len(report.files)
    verdict = "❌ **FAIL**" if report.exit_code else "✅ **PASS**"
    return (
        f"{verdict} — {n} USD file{'s' if n != 1 else ''} checked: "
        f"{counts['error']} error(s), {counts['warning']} warning(s), {counts['info']} info "
        f"(fail_on: `{report.fail_on}`)"
    )


def render(
    report: Report,
    max_bytes: int = DEFAULT_MAX_BYTES,
    title: str = "## USD asset gate",
    overflow_hint: str = "the JSON report and the job log",
) -> str:
    head = [title, "", headline(report), ""]
    with_findings = [p for p in report.files if report.findings_for(p)]
    if with_findings:
        head += ["| File | Errors | Warnings | Info |", "|---|---:|---:|---:|"]
        for path in with_findings:
            per = {"error": 0, "warning": 0, "info": 0}
            for f in report.findings_for(path):
                per[f.severity.value] += 1
            head.append(f"| {code(path)} | {per['error']} | {per['warning']} | {per['info']} |")
        head.append("")
    clean = len(report.files) - len(with_findings)
    if clean:
        head += [f"{clean} file{'s' if clean != 1 else ''} with no findings.", ""]
    if not report.findings:
        return "\n".join(head) + "\n"

    rows = []
    for f in report.findings:
        where = code(f.prim_path) if f.prim_path else ""
        if f.line is not None:
            where = f"line {f.line} " + where
        message = cell(f.message) + f" ({code(f.code)})"
        if f.fixer_available:
            message += " — an OpenUSD fixer exists (not applied)"
        rows.append(
            f"| {_ICON[f.severity.value]} {f.severity.value} | {code(f.file)} | {where} | "
            f"{code(f.rule)} | {message} |"
        )
    table_head = [
        f"<details open><summary>Findings ({len(rows)})</summary>",
        "",
        "| Severity | File | Where | Rule | Message |",
        "|---|---|---|---|---|",
    ]
    tail = ["", "</details>", ""]

    def assemble(kept: int) -> str:
        body = head + table_head + rows[:kept]
        if kept < len(rows):
            body.append("")
            body.append(
                f"_{len(rows) - kept} more finding(s) not shown to stay under the size "
                f"limit; see {overflow_hint}._"
            )
        return "\n".join(body + tail)

    kept = len(rows)
    text = assemble(kept)
    while kept > 0 and len(text.encode("utf-8")) > max_bytes:
        # Drop rows proportionally to the overshoot, at least one per pass.
        over = len(text.encode("utf-8")) - max_bytes
        avg = max(1, len("\n".join(rows[:kept]).encode("utf-8")) // max(kept, 1))
        kept = max(0, kept - max(1, over // avg + 1))
        text = assemble(kept)
    return text


def append_to(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
