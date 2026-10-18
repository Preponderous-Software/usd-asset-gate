"""``prim-naming``: prim names must match a regex chosen by prim type and/or path.

Patterns are tried in order and the first whose ``match`` fits the prim wins; a
prim no pattern matches is not checked. ``match.type`` is a glob on the prim's
type name (``"*"`` also matches untyped prims); ``match.path`` is a path glob on
the prim path (``/World/**``). The ``regex`` is applied with ``re.search``, so
anchor it with ``^``/``$``. Prims are visited with ``stage.Traverse()`` (active,
loaded, defined, non-abstract; instance proxies are not visited).
"""

from __future__ import annotations

import fnmatch
import re

from ...discovery import glob_to_regex
from ...model import Finding, Severity, severity_of

DEFAULT_MAX_FINDINGS = 50


def compile_patterns(patterns: list[dict]) -> list[tuple[str | None, re.Pattern | None, re.Pattern]]:
    compiled = []
    for p in patterns:
        match = p.get("match", {})
        path = match.get("path")
        compiled.append(
            (
                match.get("type"),
                glob_to_regex(path) if path else None,
                re.compile(p["regex"]),
            )
        )
    return compiled


def check(ctx, settings: dict) -> list[Finding]:
    patterns = settings.get("patterns") or []
    if not patterns:
        return []
    compiled = compile_patterns(patterns)
    cap = settings.get("max_findings", DEFAULT_MAX_FINDINGS)
    severity = severity_of(settings, Severity.WARNING)
    findings = []
    hidden = 0
    for prim in ctx.stage.Traverse():
        type_name = str(prim.GetTypeName())
        path = str(prim.GetPath())
        for type_glob, path_re, name_re in compiled:
            if type_glob is not None and not fnmatch.fnmatchcase(type_name, type_glob):
                continue
            if path_re is not None and not path_re.match(path):
                continue
            name = prim.GetName()
            if not name_re.search(name):
                if len(findings) < cap:
                    shown = type_name or "untyped"
                    findings.append(
                        Finding(
                            rule="prim-naming",
                            code="PrimNameMismatch",
                            severity=severity,
                            file=ctx.path,
                            message=f"{shown} prim name '{name}' does not match "
                            f"{name_re.pattern}",
                            prim_path=path,
                        )
                    )
                else:
                    hidden += 1
            break  # first matching pattern wins
    if hidden:
        findings.append(
            Finding(
                rule="prim-naming",
                code="PrimNamingFindingsCapped",
                severity=Severity.INFO,
                file=ctx.path,
                message=f"{hidden} more prim name(s) do not match; only the first {cap} "
                "are listed (raise prim-naming.max_findings to see more)",
            )
        )
    return findings


def validate_settings(settings: dict, where: str) -> None:
    """Regexes must compile; raises ValueError naming the key."""
    for i, p in enumerate(settings.get("patterns") or []):
        try:
            re.compile(p["regex"])
        except re.error as exc:
            raise ValueError(f"{where}.patterns[{i}].regex: invalid regex: {exc}") from None
