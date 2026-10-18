"""Rule IDs, default settings, parameter schemas and the alias table.

Three kinds of rule exist:

* **upstream** — one per OpenUSD validator, ``upstream:<plugin>:<validator>``,
  discovered at runtime (see :mod:`usd_asset_gate.rules.upstream`);
* **alias** — a friendly name that *claims* some upstream errors and reports them
  under its own ID, so a config reads naturally and a problem two validators both
  detect is reported once. An alias may also carry house checks of its own;
* **house** — checks with no upstream equivalent (budgets, naming, file size).

``stage-open`` and ``upstream`` are built in: they report files that cannot be
opened and validator runs that fail, and cannot be configured.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..model import Finding, Severity
from .house import (
    default_prim,
    file_size,
    instance_budget,
    prim_budget,
    prim_naming,
    stage_metadata,
)

SEVERITY_SCHEMA = {"enum": [s.value for s in Severity]}


@dataclass(frozen=True)
class Claim:
    """Upstream errors an alias takes over: one validator, some codes, an optional test."""

    validator: str
    codes: tuple[str, ...]
    applies: Callable[[Any, Any], bool] | None = None  # (RawError, AssetContext) -> bool

    def matches(self, error, ctx) -> bool:
        if error.validator != self.validator or error.name not in self.codes:
            return False
        return self.applies(error, ctx) if self.applies else True


@dataclass(frozen=True)
class RuleSpec:
    id: str
    kind: str  # "alias" | "house"
    description: str
    severity: Severity = Severity.ERROR
    params: dict[str, dict] = field(default_factory=dict)  # name -> JSON Schema
    defaults: dict[str, Any] = field(default_factory=dict)
    claims: tuple[Claim, ...] = ()
    #: Turn the claimed upstream errors into findings (aliases).
    report_claimed: Callable[..., list[Finding]] | None = None
    #: House check run on the opened stage: (ctx, settings) -> findings.
    check: Callable[..., Iterable[Finding]] | None = None
    #: House check run from ``os.stat`` before the stage is opened.
    check_before_open: Callable[..., Iterable[Finding]] | None = None
    #: Extra validation the schema cannot express: (settings, key_path) -> None,
    #: raising ValueError with a message that names the key.
    validate_settings: Callable[[dict, str], None] | None = None

    @property
    def claimed_validators(self) -> set[str]:
        return {c.validator for c in self.claims}

    def default_settings(self) -> dict[str, Any]:
        return {"enabled": True, "severity": self.severity.value, **self.defaults}

    def schema(self) -> dict:
        return {
            "type": "object",
            "description": self.description,
            "additionalProperties": False,
            "properties": {
                "enabled": {"type": "boolean"},
                "severity": SEVERITY_SCHEMA,
                **self.params,
            },
        }


# --- alias helpers ---------------------------------------------------------

_AT_PATH = re.compile(r"@([^@]+)@")
_QUOTED = re.compile(r"'([^']+)'")


def _dependency_path(error) -> str | None:
    """The unresolved asset path named in an upstream message."""
    if error.name == "CompositionError":
        m = _AT_PATH.search(error.message)
    else:
        m = _QUOTED.search(error.message)
    return m.group(1) if m else None


def unresolved_sublayer_paths(ctx) -> set[str]:
    """Sublayer asset paths, as the working-directory-relative paths messages use."""
    from pxr import Sdf

    paths = set()
    for layer in ctx.stage.GetUsedLayers():
        for sub in layer.subLayerPaths:
            resolved = Sdf.ComputeAssetPathRelativeToLayer(layer, sub)
            paths.add(ctx.relativize(resolved))
    return paths


def _is_sublayer_error(error, ctx) -> bool:
    if error.name == "CompositionError":
        return "Could not load sublayer" in error.message
    return _dependency_path(error) in unresolved_sublayer_paths(ctx)


def _is_reference_error(error, ctx) -> bool:
    if error.name == "CompositionError":
        return "for reference introduced by" in error.message or (
            "for payload introduced by" in error.message
        )
    return not _is_sublayer_error(error, ctx)


def _report_dependencies(label: str) -> Callable[..., list[Finding]]:
    """One finding per unresolved asset path, however many validators saw it."""

    def report(spec: RuleSpec, ctx, errors, severity: Severity) -> list[Finding]:
        groups: dict[str, list] = {}
        for e in errors:
            groups.setdefault(_dependency_path(e) or e.message, []).append(e)
        findings = []
        for dep, members in sorted(groups.items()):
            prim = next((m.prim_path for m in members if m.prim_path), None)
            layer = next((m.layer for m in members if m.layer), None)
            message = f"{label} '{dep}' cannot be resolved"
            if prim:
                message += f" (introduced at <{prim}>)"
            findings.append(
                Finding(
                    rule=spec.id,
                    code="UnresolvableDependency",
                    severity=severity,
                    file=ctx.path,
                    message=message,
                    prim_path=prim,
                    layer=layer,
                    fixer_available=any(m.fixer_available for m in members),
                )
            )
        return findings

    return report


def _report_each(spec: RuleSpec, ctx, errors, severity: Severity) -> list[Finding]:
    from .upstream import to_finding

    return [
        Finding(**{**to_finding(ctx, e, spec.id).to_dict(), "severity": severity})
        for e in errors
    ]


_COMPOSITION = "usdValidation:CompositionErrorTest"
_MISSING_REF = "usdUtilsValidators:MissingReferenceValidator"

ALIASES: tuple[RuleSpec, ...] = (
    RuleSpec(
        id="unresolved-references",
        kind="alias",
        description="References and payloads that point at files which cannot be found "
        f"({_MISSING_REF}, {_COMPOSITION}).",
        claims=(
            Claim(_MISSING_REF, ("UnresolvableDependency",), _is_reference_error),
            Claim(_COMPOSITION, ("CompositionError",), _is_reference_error),
        ),
        report_claimed=_report_dependencies("reference"),
    ),
    RuleSpec(
        id="unresolved-sublayers",
        kind="alias",
        description=f"Sublayers that cannot be loaded ({_COMPOSITION}, {_MISSING_REF}).",
        claims=(
            Claim(_COMPOSITION, ("CompositionError",), _is_sublayer_error),
            Claim(_MISSING_REF, ("UnresolvableDependency",), _is_sublayer_error),
        ),
        report_claimed=_report_dependencies("sublayer"),
    ),
    RuleSpec(
        id="default-prim",
        kind="alias",
        description="The stage has a valid defaultPrim (usdValidation:StageMetadataChecker), "
        "optionally of a required kind.",
        params={
            "kind": {
                "type": "string",
                "description": "Required kind on the default prim; derived kinds also pass.",
            }
        },
        claims=(Claim("usdValidation:StageMetadataChecker", ("MissingDefaultPrim",)),),
        report_claimed=_report_each,
        check=default_prim.check,
    ),
    RuleSpec(
        id="stage-metadata",
        kind="alias",
        description="upAxis and metersPerUnit are authored "
        "(usdGeomValidators:StageMetadataChecker) and, when configured, have the "
        "required values.",
        params={
            "metersPerUnit": {"type": "number", "exclusiveMinimum": 0},
            "upAxis": {"enum": ["Y", "Z"]},
            "tolerance": {
                "type": "number",
                "minimum": 0,
                "description": "Absolute tolerance for metersPerUnit (default 1e-9).",
            },
        },
        claims=(
            Claim(
                "usdGeomValidators:StageMetadataChecker",
                ("MissingUpAxisMetadata", "MissingMetersPerUnitMetadata"),
            ),
        ),
        report_claimed=_report_each,
        check=stage_metadata.check,
    ),
)

_SIZE = {
    "anyOf": [
        {"type": "integer", "minimum": 0},
        {"type": "string", "pattern": file_size.SIZE_PATTERN},
    ]
}

HOUSE: tuple[RuleSpec, ...] = (
    RuleSpec(
        id="file-size",
        kind="house",
        description="Each file stays under a size limit, checked before the stage is opened.",
        params={
            "max_bytes": {
                "description": "Bytes or a size such as 50MB (SI) / 2GiB (IEC), or a mapping "
                "from extension (.usd, .usda, .usdc, .usdz) or 'default' to a size.",
                "anyOf": [
                    _SIZE,
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"default": _SIZE},
                        "patternProperties": {r"^\.usd[acz]?$": _SIZE},
                    },
                ],
            }
        },
        check_before_open=file_size.check_before_open,
    ),
    RuleSpec(
        id="prim-budget",
        kind="house",
        severity=Severity.WARNING,
        description="Total and per-type prim counts stay within budget "
        "(UsdUtils.ComputeUsdStageStats, prototypes included).",
        params={
            "max_prims": {"type": "integer", "minimum": 0},
            "max_by_type": {
                "type": "object",
                "description": "Prim type name -> maximum count, e.g. {Mesh: 5000}.",
                "additionalProperties": {"type": "integer", "minimum": 0},
            },
        },
        check=prim_budget.check,
    ),
    RuleSpec(
        id="instance-budget",
        kind="house",
        severity=Severity.WARNING,
        description="Instancing stays within budget: a ceiling on prototypes and a floor "
        "on instancedModelCount / modelCount.",
        params={
            "max_prototypes": {"type": "integer", "minimum": 0},
            "min_instance_ratio": {"type": "number", "minimum": 0, "maximum": 1},
        },
        check=instance_budget.check,
    ),
    RuleSpec(
        id="prim-naming",
        kind="house",
        severity=Severity.WARNING,
        description="Prim names match a regex chosen by prim type and/or path glob; the "
        "first matching pattern wins.",
        params={
            "patterns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["regex"],
                    "properties": {
                        "match": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "description": "Glob on the prim type name; '*' = any.",
                                },
                                "path": {
                                    "type": "string",
                                    "description": "Glob on the prim path, e.g. /World/**.",
                                },
                            },
                        },
                        "regex": {"type": "string"},
                    },
                },
            },
            "max_findings": {
                "type": "integer",
                "minimum": 1,
                "description": "Naming findings listed per file before the rest are "
                "summarised (default 50).",
            },
        },
        check=prim_naming.check,
        validate_settings=prim_naming.validate_settings,
    ),
)

#: Every configurable non-upstream rule, in display order.
RULES: tuple[RuleSpec, ...] = ALIASES + HOUSE

BY_ID: dict[str, RuleSpec] = {r.id: r for r in RULES}


def get(rule_id: str) -> RuleSpec | None:
    return BY_ID.get(rule_id)
