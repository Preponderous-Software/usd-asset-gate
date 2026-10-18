"""``prim-budget``: total and per-type prim counts.

Counts come from ``UsdUtils.ComputeUsdStageStats`` (computed once per file and
shared through the ``AssetContext``): ``totalPrimCount`` covers the composed
stage plus instancing prototypes, without the pseudo-root, and per-type counts
add the prototype counts to the primary ones.
"""

from __future__ import annotations

from ...model import Finding, Severity, severity_of


def counts_by_type(stats: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for section in ("primary", "prototypes"):
        for type_name, n in stats.get(section, {}).get("primCountsByType", {}).items():
            counts[type_name] = counts.get(type_name, 0) + n
    return counts


def check(ctx, settings: dict) -> list[Finding]:
    max_prims = settings.get("max_prims")
    max_by_type = settings.get("max_by_type") or {}
    if max_prims is None and not max_by_type:
        return []
    stats = ctx.stats
    severity = severity_of(settings, Severity.WARNING)
    findings = []
    total = stats["totalPrimCount"]
    if max_prims is not None and total > max_prims:
        findings.append(
            Finding(
                rule="prim-budget",
                code="PrimBudgetExceeded",
                severity=severity,
                file=ctx.path,
                message=f"{total} prims; the budget is {max_prims}",
            )
        )
    by_type = counts_by_type(stats)
    for type_name, limit in sorted(max_by_type.items()):
        n = by_type.get(type_name, 0)
        if n > limit:
            findings.append(
                Finding(
                    rule="prim-budget",
                    code="PrimTypeBudgetExceeded",
                    severity=severity,
                    file=ctx.path,
                    message=f"{n} {type_name} prims; the budget is {limit}",
                )
            )
    return findings
