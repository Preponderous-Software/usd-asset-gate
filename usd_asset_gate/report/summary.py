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
    """Render the summary, never longer than ``max_bytes`` once UTF-8 encoded.

    When it would not fit, finding rows are dropped first, then per-file rows,
    each replaced by a line saying how many were left out.
    """
    with_findings = [p for p in report.files if report.findings_for(p)]
    clean = len(report.files) - len(with_findings)

    file_rows = []
    for path in with_findings:
        per = {"error": 0, "warning": 0, "info": 0}
        for f in report.findings_for(path):
            per[f.severity.value] += 1
        file_rows.append(f"| {code(path)} | {per['error']} | {per['warning']} | {per['info']} |")

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

    def assemble(kept_files: int, kept_rows: int) -> str:
        out = [title, "", headline(report), ""]
        if file_rows:
            out += ["| File | Errors | Warnings | Info |", "|---|---:|---:|---:|"]
            out += file_rows[:kept_files]
            if kept_files < len(file_rows):
                out += [
                    "",
                    f"_{len(file_rows) - kept_files} more file(s) with findings not shown "
                    f"to stay under the size limit; see {overflow_hint}._",
                ]
            out.append("")
        if clean:
            out += [f"{clean} file{'s' if clean != 1 else ''} with no findings.", ""]
        if rows:
            out += [
                f"<details open><summary>Findings ({len(rows)})</summary>",
                "",
                "| Severity | File | Where | Rule | Message |",
                "|---|---|---|---|---|",
            ]
            out += rows[:kept_rows]
            if kept_rows < len(rows):
                out += [
                    "",
                    f"_{len(rows) - kept_rows} more finding(s) not shown to stay under the "
                    f"size limit; see {overflow_hint}._",
                ]
            out += ["", "</details>"]
        return "\n".join(out) + "\n"

    def size(kept_files: int, kept_rows: int) -> int:
        return len(assemble(kept_files, kept_rows).encode("utf-8"))

    def largest_fitting(count: int, fits) -> int:
        lo, hi = 0, count  # the largest k in [0, count] with fits(k), else 0
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if fits(mid):
                lo = mid
            else:
                hi = mid - 1
        return lo

    kept_files, kept_rows = len(file_rows), len(rows)
    if size(kept_files, kept_rows) > max_bytes:
        kept_rows = largest_fitting(len(rows), lambda k: size(kept_files, k) <= max_bytes)
    if size(kept_files, kept_rows) > max_bytes:
        kept_rows = 0
        kept_files = largest_fitting(len(file_rows), lambda k: size(k, 0) <= max_bytes)
    text = assemble(kept_files, kept_rows)
    if len(text.encode("utf-8")) > max_bytes:  # not even the skeleton fits
        text = "\n".join(
            [title, "", headline(report), "",
             f"_Details omitted to stay under the size limit; see {overflow_hint}._", ""]
        )
        text = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return text


def append_to(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
