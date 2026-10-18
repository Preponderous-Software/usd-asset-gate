"""Stable JSON report, also the input of the ``comment`` subcommand.

Top-level shape (``version`` 1)::

    {
      "version": 1,
      "tool": {"name": "usd-asset-gate", "version": "..."},
      "files": [{"path": "...", "findings": 0, "diagnostics": ["..."]}],
      "findings": [{"rule": ..., "code": ..., "severity": ..., "file": ..., ...}],
      "summary": {"files": 1, "error": 0, "warning": 0, "info": 0,
                  "fail_on": "error", "exit_code": 0, "passed": true}
    }
"""

from __future__ import annotations

import json
from typing import Any

from .. import __version__
from ..model import Finding, Report

REPORT_VERSION = 1


def to_dict(report: Report) -> dict[str, Any]:
    per_file = {path: 0 for path in report.files}
    for f in report.findings:
        per_file[f.file] = per_file.get(f.file, 0) + 1
    counts = report.counts()
    return {
        "version": REPORT_VERSION,
        "tool": {"name": "usd-asset-gate", "version": __version__},
        "files": [
            {"path": p, "findings": n, "diagnostics": report.diagnostics.get(p, [])}
            for p, n in per_file.items()
        ],
        "findings": [f.to_dict() for f in report.findings],
        "summary": {
            "files": len(report.files),
            **counts,
            "fail_on": report.fail_on,
            "exit_code": report.exit_code,
            "passed": report.exit_code == 0,
        },
    }


def render(report: Report) -> str:
    return json.dumps(to_dict(report), indent=2) + "\n"


def from_dict(data: dict[str, Any]) -> Report:
    """Rebuild a ``Report`` from :func:`to_dict` output (used by ``comment``)."""
    if data.get("version") != REPORT_VERSION:
        raise ValueError(
            f"unsupported report version {data.get('version')!r} (expected {REPORT_VERSION})"
        )
    return Report(
        files=[entry["path"] for entry in data["files"]],
        findings=[Finding.from_dict(f) for f in data["findings"]],
        fail_on=data["summary"]["fail_on"],
        diagnostics={e["path"]: list(e.get("diagnostics", [])) for e in data["files"]},
    )
