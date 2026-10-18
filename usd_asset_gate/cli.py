"""Command-line front end: ``check`` and ``rules``.

Exit codes: 0 pass, 1 findings at or above ``fail_on``, 2 usage or config error.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

from . import __version__
from .discovery import DiscoveryError, expand_paths
from .model import FAIL_ON_VALUES

EXIT_PASS = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

#: Oldest OpenUSD release supported (``usd-core`` 25.8, i.e. OpenUSD v25.08).
MIN_USD_VERSION = (0, 25, 8)


class UsageError(Exception):
    """Reported on stderr; the CLI exits 2."""


def require_pxr() -> None:
    """Fail with an install hint unless a supported ``pxr`` is importable."""
    try:
        from pxr import Usd  # noqa: F401
    except ImportError as exc:
        raise UsageError(
            "the OpenUSD Python bindings (pxr) could not be imported "
            f"({exc}). Install them with: pip install usd-core"
        ) from None
    version = tuple(Usd.GetVersion())
    if version < MIN_USD_VERSION:
        have = ".".join(str(v) for v in version[1:])
        raise UsageError(
            f"usd-core {have} is too old; usd-asset-gate needs usd-core >= 25.8 "
            "(pip install -U usd-core)"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="usd-asset-gate",
        description="CI quality gate for OpenUSD assets.",
        epilog="Exit codes: 0 pass, 1 findings at or above fail_on, 2 usage or config error.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    config_help = "config file (default: .usd-asset-gate.yml in the working directory, if any)"

    check = sub.add_parser("check", help="check USD files and directories")
    check.add_argument("paths", nargs="*", metavar="PATH", help="files or directories to check")
    check.add_argument("--config", metavar="FILE", help=config_help)
    check.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format on stdout (default: text)",
    )
    check.add_argument(
        "--fail-on",
        choices=FAIL_ON_VALUES,
        default=None,
        help="lowest severity that makes the run exit 1 (default: fail_on from the config, "
        "else error)",
    )
    check.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="also print the diagnostics OpenUSD wrote while opening and validating (stderr)",
    )

    rules = sub.add_parser("rules", help="list every available rule and its effective setting")
    rules.add_argument("--config", metavar="FILE", help=config_help)
    rules.add_argument(
        "--path",
        metavar="PATH",
        help="show the settings that apply to this file (with per-path overrides)",
    )
    rules.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def load_config(path: str | None):
    """Load and fully validate the config; exits 2 (UsageError) before any asset is opened."""
    from .config import ConfigError, check_upstream_names, load
    from .rules import upstream

    try:
        config = load(path)
        check_upstream_names(config, [v.name for v in upstream.all_validators()])
    except ConfigError as exc:
        raise UsageError(f"invalid config: {exc}") from None
    return config


def discover(paths: list[str], config) -> list[str]:
    """Expand paths; directory contents are filtered by ``include``/``exclude``.

    A file named explicitly on the command line is always checked.
    """
    from .discovery import filter_paths

    try:
        expanded = expand_paths(paths)
    except DiscoveryError as exc:
        raise UsageError(str(exc)) from None
    explicit = [p for p, is_explicit in expanded if is_explicit]
    walked = filter_paths(
        [p for p, is_explicit in expanded if not is_explicit], config.include, config.exclude
    )
    return sorted(set(explicit) | set(walked))


def cmd_check(args: argparse.Namespace) -> int:
    require_pxr()
    from .report import json as json_report
    from .report import text as text_report
    from .runner import Runner

    config = load_config(args.config)
    if not args.paths:
        raise UsageError("no paths given (pass files or directories to check)")
    files = discover(args.paths, config)
    report = Runner(config).run(files, fail_on=args.fail_on)
    if args.verbose:
        for path, lines in report.diagnostics.items():
            for line in lines:
                sys.stderr.write(f"{path}: openusd: {line}\n")
    if args.format == "json":
        sys.stdout.write(json_report.render(report))
    else:
        sys.stdout.write(text_report.render(report, colour=text_report.use_colour(sys.stdout)))
    return report.exit_code


def rules_table(config, path: str | None) -> list[dict]:
    """Every rule with its effective settings, in display order."""
    from .rules import registry
    from .runner import STAGE_OPEN_RULE, Runner
    from .rules.upstream import RUN_FAILED_RULE

    settings = config.rule_settings(path)
    runner = Runner(config)
    selected = runner._upstream_for(settings)
    claimed_by = {}
    for spec in registry.RULES:
        for v in spec.claimed_validators:
            claimed_by.setdefault(v, []).append(spec.id)
    rows = [
        {
            "rule": STAGE_OPEN_RULE,
            "kind": "builtin",
            "enabled": True,
            "severity": "error",
            "description": "The file can be opened as a USD stage.",
        },
        {
            "rule": RUN_FAILED_RULE,
            "kind": "builtin",
            "enabled": True,
            "severity": "error",
            "description": "OpenUSD could run the validators on the stage.",
        },
    ]
    for spec in registry.RULES:
        s = settings[spec.id]
        params = {k: v for k, v in s.items() if k not in ("enabled", "severity")}
        rows.append(
            {
                "rule": spec.id,
                "kind": spec.kind,
                "enabled": bool(s.get("enabled", True)),
                "severity": s.get("severity", spec.severity.value),
                "description": spec.description,
                **({"params": params} if params else {}),
            }
        )
    for info in runner.available:
        s = settings.get(info.rule, {})
        row = {
            "rule": info.rule,
            "kind": "upstream",
            "enabled": info.name in selected,
            "severity": s.get("severity", "as reported"),
            "description": " ".join(info.doc.split()),
            "keywords": list(info.keywords),
        }
        if info.name in claimed_by:
            row["reported_under"] = claimed_by[info.name]
        rows.append(row)
    return rows


def cmd_rules(args: argparse.Namespace) -> int:
    require_pxr()
    import json

    config = load_config(args.config)
    path = args.path.replace(os.sep, "/") if args.path else None
    rows = rules_table(config, path)
    if args.format == "json":
        payload = {"config": config.source, "path": path, "fail_on": config.fail_on, "rules": rows}
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return EXIT_PASS
    sys.stdout.write(f"config: {config.source or '(defaults)'}\n")
    if path:
        sys.stdout.write(f"path: {path}\n")
    sys.stdout.write(f"fail_on: {config.fail_on}\n\n")
    for r in rows:
        state = "enabled" if r["enabled"] else "disabled"
        line = f"{r['rule']}  [{r['kind']}, {state}, severity {r['severity']}]"
        if r.get("params"):
            line += "  " + json.dumps(r["params"], sort_keys=True)
        if r.get("reported_under"):
            line += "  (claimed by " + ", ".join(r["reported_under"]) + " when enabled)"
        sys.stdout.write(line + "\n")
    return EXIT_PASS


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = {"check": cmd_check, "rules": cmd_rules}[args.command]
    try:
        return handler(args)
    except UsageError as exc:
        sys.stderr.write(f"usd-asset-gate: error: {exc}\n")
        return EXIT_USAGE
