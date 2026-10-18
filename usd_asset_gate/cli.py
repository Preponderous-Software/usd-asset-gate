"""Command-line front end: ``check`` and ``rules``.

Exit codes: 0 pass, 1 findings at or above ``fail_on``, 2 usage or config error.
"""

from __future__ import annotations

import argparse
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

    check = sub.add_parser("check", help="check USD files and directories")
    check.add_argument("paths", nargs="*", metavar="PATH", help="files or directories to check")
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
        help="lowest severity that makes the run exit 1 (default: error)",
    )
    check.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="also print the diagnostics OpenUSD wrote while opening and validating (stderr)",
    )

    rules = sub.add_parser("rules", help="list every available rule and its effective setting")
    rules.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def cmd_check(args: argparse.Namespace) -> int:
    require_pxr()
    from .report import json as json_report
    from .report import text as text_report
    from .runner import run

    if not args.paths:
        raise UsageError("no paths given (pass files or directories to check)")
    try:
        files = [path for path, _ in expand_paths(args.paths)]
    except DiscoveryError as exc:
        raise UsageError(str(exc)) from None
    report = run(files, fail_on=args.fail_on or "error")
    if args.verbose:
        for path, lines in report.diagnostics.items():
            for line in lines:
                sys.stderr.write(f"{path}: openusd: {line}\n")
    if args.format == "json":
        sys.stdout.write(json_report.render(report))
    else:
        sys.stdout.write(text_report.render(report, colour=text_report.use_colour(sys.stdout)))
    return report.exit_code


def cmd_rules(args: argparse.Namespace) -> int:
    require_pxr()
    import json

    from .runner import STAGE_OPEN_RULE

    rules = [{"rule": STAGE_OPEN_RULE, "enabled": True, "severity": "error"}]
    if args.format == "json":
        sys.stdout.write(json.dumps({"rules": rules}, indent=2) + "\n")
    else:
        for r in rules:
            sys.stdout.write(f"{r['rule']}: enabled, severity {r['severity']}\n")
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
