"""Adapter over OpenUSD's validation framework (``pxr.UsdValidation``).

The validators are the ones ``usdchecker`` runs; the ``usd-core`` wheel ships the
Python module but no ``usdchecker`` executable, so they are run in-process here.
Every registered validator becomes one rule, ``upstream:<plugin>:<validator>``,
discovered from the registry at runtime.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Iterable

from ..model import Finding, Severity
from ..stage import AssetContext, capture_native_stderr, tf_error_messages

RULE_PREFIX = "upstream:"


@dataclass(frozen=True)
class ValidatorInfo:
    name: str  # "<plugin>:<validator>", as the registry names it
    keywords: tuple[str, ...]
    doc: str
    is_suite: bool

    @property
    def rule(self) -> str:
        return RULE_PREFIX + self.name


def rule_id(validator_name: str) -> str:
    return RULE_PREFIX + validator_name


def all_validators() -> list[ValidatorInfo]:
    """Every validator registered in the installed ``usd-core``, sorted by name.

    Suites are left out: a suite only groups validators that are listed anyway.
    """
    from pxr import UsdValidation

    infos = []
    for meta in UsdValidation.ValidationRegistry().GetAllValidatorMetadata():
        if meta.isSuite:
            continue
        infos.append(
            ValidatorInfo(
                name=meta.name,
                keywords=tuple(sorted(meta.GetKeywords())),
                doc=(meta.doc or "").strip(),
                is_suite=bool(meta.isSuite),
            )
        )
    return sorted(infos, key=lambda v: v.name)


def _matches_any(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def select_validators(
    available: Iterable[ValidatorInfo],
    validators: Iterable[str] = (),
    keywords: Iterable[str] = (),
    disable: Iterable[str] = (),
) -> list[str]:
    """Choose validator names.

    A validator is selected when it matches one of ``validators`` (names or
    globs) or carries one of ``keywords``; when both are empty every validator
    is selected. Anything matching ``disable`` (names or globs) is then removed.
    """
    validators = list(validators)
    keywords = set(keywords)
    disable = list(disable)
    chosen = []
    for info in available:
        if validators or keywords:
            picked = _matches_any(info.name, validators) or bool(keywords & set(info.keywords))
        else:
            picked = True
        if picked and not _matches_any(info.name, disable):
            chosen.append(info.name)
    return chosen


@dataclass(frozen=True)
class RawError:
    """One ``ValidationError``, flattened to plain Python values."""

    validator: str
    name: str
    severity: Severity
    message: str
    prim_path: str | None
    layer: str | None
    fixer_available: bool


def _severity(error_type) -> Severity | None:
    from pxr import UsdValidation

    mapping = {
        UsdValidation.ValidationErrorType.Error: Severity.ERROR,
        UsdValidation.ValidationErrorType.Warn: Severity.WARNING,
        UsdValidation.ValidationErrorType.Info: Severity.INFO,
    }
    return mapping.get(error_type)


def _sites(ctx: AssetContext, error) -> tuple[str | None, str | None]:
    """First prim/property path and first non-root layer named by the error's sites."""
    prim_path = None
    layer = None
    root_id = ctx.stage.GetRootLayer().identifier if ctx.stage else None
    for site in error.GetSites():
        if prim_path is None:
            if site.IsProperty():
                prim_path = str(site.GetProperty().GetPath())
            elif site.IsPrim():
                path = str(site.GetPrim().GetPath())
                if path != "/":
                    prim_path = path
        if layer is None:
            site_layer = site.GetLayer()
            if site_layer and site_layer.identifier != root_id:
                layer = ctx.relativize(site_layer.identifier)
    return prim_path, layer


#: How often a validation run is retried after a Tf error raised by OpenUSD itself.
VALIDATE_ATTEMPTS = 3


def _validate(ctx: AssetContext, context) -> list:
    """``context.Validate(stage)``, retried when OpenUSD raises a ``Tf`` error.

    ``usd-core`` 25.8 can raise "UsdShade Connectable behavior already
    registered" when validators running in parallel register the same schema
    behaviour for the first time (observed on 2026-10-17 with the fixture
    corpus); the registration has happened by then, so a second run succeeds.
    """
    from pxr import Tf

    for attempt in range(VALIDATE_ATTEMPTS):
        try:
            with capture_native_stderr(ctx.diagnostics):
                return list(context.Validate(ctx.stage))
        except Tf.ErrorException as exc:
            if attempt == VALIDATE_ATTEMPTS - 1:
                raise ValidationRunError("; ".join(tf_error_messages(exc))) from None
    return []  # unreachable


class ValidationRunError(Exception):
    """OpenUSD failed to run the validators on a stage (reported as a finding)."""


def run_validators(ctx: AssetContext, names: Iterable[str]) -> list[RawError]:
    """Run the named validators on ``ctx.stage`` and return their errors.

    Fixers are never applied; ``fixer_available`` only records that OpenUSD
    offers one (``GetFixers`` exists from ``usd-core`` 26.x).
    """
    from pxr import UsdValidation

    names = sorted(set(names))
    if not names or ctx.stage is None:
        return []
    registry = UsdValidation.ValidationRegistry()
    validators = [v for v in registry.GetOrLoadValidatorsByName(names) if v]
    if not validators:
        return []
    errors = _validate(ctx, UsdValidation.ValidationContext(validators))
    raw = []
    for error in errors:
        severity = _severity(error.GetType())
        if severity is None:
            continue
        validator = error.GetValidator()
        validator_name = validator.GetMetadata().name if validator else "unknown"
        prim_path, layer = _sites(ctx, error)
        fixers = error.GetFixers() if hasattr(error, "GetFixers") else []
        raw.append(
            RawError(
                validator=validator_name,
                name=error.GetName(),
                severity=severity,
                message=ctx.relativize(error.GetMessage()),
                prim_path=prim_path,
                layer=layer,
                fixer_available=bool(fixers),
            )
        )
    return raw


def to_finding(ctx: AssetContext, error: RawError, rule: str | None = None) -> Finding:
    return Finding(
        rule=rule or rule_id(error.validator),
        code=error.name,
        severity=error.severity,
        file=ctx.path,
        message=error.message,
        prim_path=error.prim_path,
        layer=error.layer,
        fixer_available=error.fixer_available,
    )


#: Rule ID reported when OpenUSD fails to run the validators at all.
RUN_FAILED_RULE = "upstream"


def run_failed_finding(ctx: AssetContext, exc: ValidationRunError) -> Finding:
    return Finding(
        rule=RUN_FAILED_RULE,
        code="ValidationRunFailed",
        severity=Severity.ERROR,
        file=ctx.path,
        message=f"OpenUSD could not run the validators: {ctx.relativize(str(exc))}",
    )


def check(ctx: AssetContext, names: Iterable[str]) -> list[Finding]:
    """Run the selected validators and report each error under its own rule ID."""
    try:
        return [to_finding(ctx, e) for e in run_validators(ctx, names)]
    except ValidationRunError as exc:
        return [run_failed_finding(ctx, exc)]
