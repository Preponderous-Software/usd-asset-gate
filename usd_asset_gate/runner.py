"""Run every enabled rule over every discovered file and collect findings."""

from __future__ import annotations

import os

from .config import Config
from .model import Finding, Report, Severity, severity_of, sort_findings
from .rules import registry, upstream
from .stage import (
    AssetContext,
    normalize_diagnostic,
    open_stage,
    read_usda_text,
    usda_prim_line,
)

#: Rule ID reported when a file cannot be opened as a USD stage at all.
STAGE_OPEN_RULE = "stage-open"


def add_line_numbers(ctx: AssetContext, findings: list[Finding]) -> list[Finding]:
    """Fill ``line`` for findings on prims of a usda root layer (best effort)."""
    if not any(f.prim_path and not f.layer and f.line is None for f in findings):
        return findings
    text = read_usda_text(ctx.abs_path)
    if text is None:
        return findings
    out = []
    for f in findings:
        if f.prim_path and not f.layer and f.line is None:
            line = usda_prim_line(text, f.prim_path)
            if line is not None:
                f = Finding(**{**f.to_dict(), "severity": f.severity, "line": line})
        out.append(f)
    return out


class Runner:
    """Checks files against one resolved configuration."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        up = self.config.upstream
        self.available = upstream.all_validators()
        if up.enabled:
            self.upstream_selected = upstream.select_validators(
                self.available, up.validators, up.keywords, up.disable
            )
        else:
            self.upstream_selected = []

    def _upstream_for(self, settings: dict[str, dict]) -> set[str]:
        """Selected validators, adjusted by per-rule ``enabled`` on ``upstream:*`` IDs."""
        names = set(self.upstream_selected)
        for info in self.available:
            explicit = settings.get(info.rule, {}).get("enabled")
            if explicit is False:
                names.discard(info.name)
            elif explicit is True:
                names.add(info.name)
        return names

    def check_file(self, path: str) -> tuple[AssetContext, list[Finding]]:
        settings = self.config.rule_settings(path)
        ctx = AssetContext(path=path, abs_path=os.path.abspath(path))
        enabled = [s for s in registry.RULES if settings[s.id].get("enabled", True)]

        findings: list[Finding] = []
        for spec in enabled:
            if spec.check_before_open:
                findings.extend(spec.check_before_open(ctx, settings[spec.id]))
        if findings:  # e.g. over the file-size limit: fail fast, do not open
            return ctx, findings

        errors = open_stage(ctx)
        if errors:
            findings.append(
                Finding(
                    rule=STAGE_OPEN_RULE,
                    code="StageOpenFailed",
                    severity=Severity.ERROR,
                    file=path,
                    message="; ".join(errors),
                )
            )
            return ctx, findings

        selected = self._upstream_for(settings)
        aliases = [s for s in enabled if s.claims]
        needed = set(selected).union(*(a.claimed_validators for a in aliases))
        try:
            raw = upstream.run_validators(ctx, needed)
        except upstream.ValidationRunError as exc:
            raw = []
            findings.append(upstream.run_failed_finding(ctx, exc))

        claimed: dict[str, list] = {a.id: [] for a in aliases}
        for error in raw:
            owner = next(
                (a for a in aliases if any(c.matches(error, ctx) for c in a.claims)), None
            )
            if owner is not None:
                claimed[owner.id].append(error)
            elif error.validator in selected:
                rule = upstream.rule_id(error.validator)
                finding = upstream.to_finding(ctx, error)
                severity = severity_of(settings.get(rule, {}), error.severity)
                if severity is not finding.severity:
                    finding = Finding(**{**finding.to_dict(), "severity": severity})
                findings.append(finding)
        for alias in aliases:
            if claimed[alias.id]:
                severity = severity_of(settings[alias.id], alias.severity)
                findings.extend(alias.report_claimed(alias, ctx, claimed[alias.id], severity))

        for spec in enabled:
            if spec.check:
                findings.extend(spec.check(ctx, settings[spec.id]))
        return ctx, add_line_numbers(ctx, findings)

    def run(self, files: list[str], fail_on: str | None = None) -> Report:
        findings: list[Finding] = []
        diagnostics: dict[str, list[str]] = {}
        for path in files:
            ctx, file_findings = self.check_file(path)
            findings.extend(file_findings)
            if ctx.diagnostics:
                diagnostics[path] = [
                    normalize_diagnostic(ctx.relativize(line)) for line in ctx.diagnostics
                ]
        return Report(
            files=list(files),
            findings=sort_findings(findings),
            fail_on=fail_on or self.config.fail_on,
            diagnostics=diagnostics,
        )


def check_file(path: str, config: Config | None = None) -> tuple[AssetContext, list[Finding]]:
    return Runner(config).check_file(path)


def run(files: list[str], config: Config | None = None, fail_on: str | None = None) -> Report:
    return Runner(config).run(files, fail_on=fail_on)
