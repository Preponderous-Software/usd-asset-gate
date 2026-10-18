"""The finding model shared by every rule and every reporter."""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


class Severity(str, enum.Enum):
    """How serious a finding is. Ordered: info < warning < error."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def rank(self) -> int:
        return _RANK[self]

    @classmethod
    def parse(cls, value: str) -> "Severity":
        try:
            return cls(value)
        except ValueError:
            raise ValueError(
                f"unknown severity {value!r} (expected one of: error, warning, info)"
            ) from None


_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.ERROR: 2}

#: Values accepted by ``fail_on``. ``never`` means the run never exits 1.
FAIL_ON_VALUES = ("error", "warning", "info", "never")


@dataclass(frozen=True)
class Finding:
    """One problem found in one checked file."""

    rule: str
    code: str
    severity: Severity
    file: str
    message: str
    prim_path: str | None = None
    layer: str | None = None
    line: int | None = None
    fixer_available: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Finding":
        return cls(
            rule=data["rule"],
            code=data["code"],
            severity=Severity.parse(data["severity"]),
            file=data["file"],
            message=data["message"],
            prim_path=data.get("prim_path"),
            layer=data.get("layer"),
            line=data.get("line"),
            fixer_available=bool(data.get("fixer_available", False)),
        )

    def sort_key(self) -> tuple:
        return (
            self.file,
            -self.severity.rank,
            self.rule,
            self.prim_path or "",
            self.code,
            self.message,
        )


def sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Stable order: file, then most severe first, then rule, prim, code, message."""
    return sorted(findings, key=Finding.sort_key)


def is_failing(finding: Finding, fail_on: str) -> bool:
    """True when ``finding`` is at or above the ``fail_on`` threshold."""
    if fail_on == "never":
        return False
    return finding.severity.rank >= Severity.parse(fail_on).rank


def exit_code_for(findings: Iterable[Finding], fail_on: str) -> int:
    """0 when nothing reaches ``fail_on``, otherwise 1."""
    return 1 if any(is_failing(f, fail_on) for f in findings) else 0


@dataclass
class Report:
    """Everything one ``check`` run produced, in a form every reporter can render."""

    files: list[str]
    findings: list[Finding]
    fail_on: str = "error"
    #: Native OpenUSD output captured per file (warnings it prints instead of raising).
    diagnostics: dict[str, list[str]] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Severity}
        for f in self.findings:
            counts[f.severity.value] += 1
        return counts

    @property
    def exit_code(self) -> int:
        return exit_code_for(self.findings, self.fail_on)

    def findings_for(self, path: str) -> list[Finding]:
        return [f for f in self.findings if f.file == path]
