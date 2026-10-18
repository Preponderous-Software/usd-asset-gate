"""``default-prim``: optionally require a ``kind`` on the default prim.

A missing or invalid default prim is reported upstream
(``usdValidation:StageMetadataChecker`` / ``MissingDefaultPrim``, claimed by this
rule's alias); this module adds the ``kind`` requirement.
"""

from __future__ import annotations

from ...model import Finding, Severity, severity_of


def check(ctx, settings: dict) -> list[Finding]:
    from pxr import Kind, Usd

    required = settings.get("kind")
    if not required:
        return []
    prim = ctx.stage.GetDefaultPrim()
    if not prim or not prim.IsValid():
        return []  # MissingDefaultPrim is already reported
    actual = Usd.ModelAPI(prim).GetKind()
    # Kind.Registry.IsA accepts kinds derived from the required one.
    if actual and Kind.Registry.IsA(actual, required):
        return []
    shown = f"'{actual}'" if actual else "no kind"
    return [
        Finding(
            rule="default-prim",
            code="DefaultPrimKindMismatch",
            severity=severity_of(settings, Severity.ERROR),
            file=ctx.path,
            message=f"default prim <{prim.GetPath()}> has {shown}; this project requires "
            f"kind '{required}'",
            prim_path=str(prim.GetPath()),
        )
    ]
