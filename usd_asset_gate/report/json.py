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
from ..model import FAIL_ON_VALUES, Finding, Report

REPORT_VERSION = 1

_JSON_TYPES = {dict: "object", list: "array", str: "string", int: "number", float: "number", bool: "boolean", type(None): "null"}


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
    """Rebuild a ``Report`` from :func:`to_dict` output (used by ``comment``).

    Raises ``ValueError`` (or ``KeyError`` for a missing field) when ``data`` is not
    shaped like a report, so ``comment`` can exit 2 instead of failing with a traceback.
    """
    if not isinstance(data, dict):
        raise ValueError(f"not a usd-asset-gate report (top level is a JSON {_json_kind(data)})")
    if data.get("version") != REPORT_VERSION:
        raise ValueError(
            f"unsupported report version {data.get('version')!r} (expected {REPORT_VERSION})"
        )
    try:
        files = _array(data["files"], "'files'")
        report = Report(
            files=[entry["path"] for entry in files],
            findings=[Finding.from_dict(f) for f in _array(data["findings"], "'findings'")],
            fail_on=data["summary"]["fail_on"],
            diagnostics={
                e["path"]: list(_array(e.get("diagnostics", []), f"'diagnostics' of {e['path']!r}"))
                for e in files
            },
        )
    except (TypeError, AttributeError) as exc:
        raise ValueError(f"malformed report: {exc}") from None
    if report.fail_on not in FAIL_ON_VALUES:
        raise ValueError(
            f"unknown fail_on {report.fail_on!r} (expected one of: {', '.join(FAIL_ON_VALUES)})"
        )
    return report


def _json_kind(value: Any) -> str:
    return _JSON_TYPES.get(type(value), type(value).__name__)


def _array(value: Any, what: str) -> list:
    """``value`` itself when it is a list; an empty object or string would otherwise iterate
    as no entries and turn a malformed report into an "all checks pass" comment."""
    if not isinstance(value, list):
        raise ValueError(f"malformed report: {what} is a JSON {_json_kind(value)}, expected an array")
    return value
