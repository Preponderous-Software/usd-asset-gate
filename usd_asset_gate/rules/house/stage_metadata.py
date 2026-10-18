"""``stage-metadata``: required ``metersPerUnit`` / ``upAxis`` values.

Presence is checked upstream (``usdGeomValidators:StageMetadataChecker``, claimed by
this rule's alias); this module checks the *values* when they are authored.
"""

from __future__ import annotations

from ...model import Finding, Severity, severity_of

DEFAULT_TOLERANCE = 1e-9


def check(ctx, settings: dict) -> list[Finding]:
    from pxr import UsdGeom

    stage = ctx.stage
    severity = severity_of(settings, Severity.ERROR)
    findings = []
    required_mpu = settings.get("metersPerUnit")
    if required_mpu is not None and UsdGeom.StageHasAuthoredMetersPerUnit(stage):
        actual = UsdGeom.GetStageMetersPerUnit(stage)
        tolerance = settings.get("tolerance", DEFAULT_TOLERANCE)
        if abs(actual - required_mpu) > tolerance:
            findings.append(
                Finding(
                    rule="stage-metadata",
                    code="MetersPerUnitMismatch",
                    severity=severity,
                    file=ctx.path,
                    message=f"metersPerUnit is {actual:g}; this project requires "
                    f"{required_mpu:g} (tolerance {tolerance:g})",
                )
            )
    required_axis = settings.get("upAxis")
    if required_axis is not None and stage.HasAuthoredMetadata("upAxis"):
        actual_axis = str(UsdGeom.GetStageUpAxis(stage))
        if actual_axis != required_axis:
            findings.append(
                Finding(
                    rule="stage-metadata",
                    code="UpAxisMismatch",
                    severity=severity,
                    file=ctx.path,
                    message=f"upAxis is {actual_axis}; this project requires {required_axis}",
                )
            )
    return findings
