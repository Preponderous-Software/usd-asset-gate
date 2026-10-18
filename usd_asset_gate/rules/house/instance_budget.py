"""``instance-budget``: prototype count and how much of the model set is instanced.

From ``UsdUtils.ComputeUsdStageStats`` (shared, computed once per file):

* ``max_prototypes`` — ceiling on ``prototypeCount``;
* ``min_instance_ratio`` — floor on ``instancedModelCount / modelCount``, i.e.
  instanced models / (instanced + uninstanced models). Skipped when the stage has
  no models.
"""

from __future__ import annotations

from ...model import Finding, Severity, severity_of


def check(ctx, settings: dict) -> list[Finding]:
    max_prototypes = settings.get("max_prototypes")
    min_ratio = settings.get("min_instance_ratio")
    if max_prototypes is None and min_ratio is None:
        return []
    stats = ctx.stats
    severity = severity_of(settings, Severity.WARNING)
    findings = []
    prototypes = stats["prototypeCount"]
    if max_prototypes is not None and prototypes > max_prototypes:
        findings.append(
            Finding(
                rule="instance-budget",
                code="PrototypeBudgetExceeded",
                severity=severity,
                file=ctx.path,
                message=f"{prototypes} instancing prototypes; the budget is {max_prototypes}",
            )
        )
    models = stats["modelCount"]
    instanced = stats["instancedModelCount"]
    if min_ratio is not None and models > 0:
        ratio = instanced / models
        if ratio < min_ratio:
            findings.append(
                Finding(
                    rule="instance-budget",
                    code="InstanceRatioTooLow",
                    severity=severity,
                    file=ctx.path,
                    message=f"{instanced} of {models} models are instanced ({ratio:.2f}); "
                    f"the minimum ratio is {min_ratio:g}",
                )
            )
    return findings
