"""Command-line front end: ``check``, ``rules``, ``comment`` and ``preview``.

Exit codes: 0 pass, 1 findings at or above ``fail_on``, 2 usage or config error.
``preview`` is advisory and never exits 1.
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
    check.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="files or directories to check (with --changed-since: limit to these)",
    )
    check.add_argument("--config", metavar="FILE", help=config_help)
    check.add_argument(
        "--changed-since",
        metavar="REF",
        help="check only USD files changed between REF and HEAD (git diff REF...HEAD; "
        "deleted files are skipped)",
    )
    check.add_argument(
        "--format",
        choices=("text", "json", "github"),
        default="text",
        help="output format on stdout (default: text). github prints workflow-command "
        "annotations followed by the text report",
    )
    check.add_argument(
        "--json", metavar="FILE", dest="json_file", help="also write the JSON report to FILE"
    )
    check.add_argument(
        "--summary",
        metavar="FILE",
        help="append a Markdown summary to FILE (e.g. $GITHUB_STEP_SUMMARY)",
    )
    check.add_argument(
        "--max-annotations",
        type=int,
        default=None,
        metavar="N",
        help="with --format github, annotate at most N findings per severity (default 10)",
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

    comment = sub.add_parser(
        "comment",
        help="create or update the single pull-request comment from a JSON report",
        description="Reads GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_API_URL and the pull "
        "request number from GITHUB_EVENT_PATH. Skips with exit 0 (and a log line) when "
        "there is no token or pull request, or when the token cannot write (403).",
    )
    comment.add_argument(
        "--from", dest="report", required=True, metavar="FILE", help="report from check --json"
    )
    comment.add_argument("--pr", type=int, metavar="N", help="pull request number (overrides the event)")
    comment.add_argument(
        "--previews",
        metavar="FILE",
        help="also add the advisory 'Visual changes' section from preview --json",
    )

    preview = sub.add_parser(
        "preview",
        help="render changed USD files before and after with Strata and diff them (advisory)",
        description="Renders each changed root layer as it was at the merge base of REF and "
        "as it is now, from the same cameras, headless with Strata (libstrata.so at "
        "STRATA_LIBRARY), and compares the images with Strata's ΔE policy. Advisory: exits 0 "
        "whatever the images show, and when Strata cannot start (reported as 'preview "
        "unavailable'); 2 only on a usage or config error. Needs the preview extra: "
        "pip install 'usd-asset-gate[preview]'.",
    )
    preview.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="with --changed-since: limit to these; without it: render these files (after only)",
    )
    preview.add_argument("--config", metavar="FILE", help=config_help)
    preview.add_argument(
        "--changed-since",
        metavar="REF",
        help="render USD files changed between REF and HEAD, before and after",
    )
    preview.add_argument("--out", required=True, metavar="DIR", help="directory for the PNG images")
    preview.add_argument("--json", metavar="FILE", dest="json_file", help="write the previews document to FILE")
    preview.add_argument(
        "--summary", metavar="FILE", help="append the Markdown section to FILE (e.g. $GITHUB_STEP_SUMMARY)"
    )
    preview.add_argument(
        "--force",
        action="store_true",
        help="run even when the config does not set preview.enabled: true",
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

    from .discovery import changed_files, filter_paths, under_any
    from .report import github as github_report
    from .report import summary as summary_report

    config = load_config(args.config)
    if args.changed_since:
        try:
            changed = changed_files(args.changed_since)
            if args.paths:
                for p in args.paths:
                    if not os.path.exists(p):
                        raise DiscoveryError(f"no such file or directory: {p}")
                changed = [c for c in changed if under_any(c, args.paths)]
        except DiscoveryError as exc:
            raise UsageError(str(exc)) from None
        files = filter_paths(changed, config.include, config.exclude)
    elif not args.paths:
        raise UsageError("no paths given (pass files or directories, or --changed-since REF)")
    else:
        files = discover(args.paths, config)
    report = Runner(config).run(files, fail_on=args.fail_on)
    if args.verbose:
        for path, lines in report.diagnostics.items():
            for line in lines:
                sys.stderr.write(f"{path}: openusd: {line}\n")
    if args.json_file:
        with open(args.json_file, "w", encoding="utf-8") as fh:
            fh.write(json_report.render(report))
    if args.summary:
        summary_report.append_to(args.summary, summary_report.render(report))
    if args.format == "json":
        sys.stdout.write(json_report.render(report))
    elif args.format == "github":
        limit = args.max_annotations
        if limit is None:
            limit = github_report.DEFAULT_MAX_PER_SEVERITY
        sys.stdout.write(github_report.render(report, max_per_severity=limit))
        sys.stdout.write(github_report.guarded(text_report.render(report)))
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
    from .discovery import display_path

    path = display_path(args.path) if args.path else None
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


def cmd_comment(args: argparse.Namespace) -> int:
    import json

    from .report import comment
    from .report import json as json_report

    try:
        with open(args.report, encoding="utf-8") as fh:
            report = json_report.from_dict(json.load(fh))
    except (OSError, ValueError, KeyError) as exc:
        raise UsageError(f"cannot read report {args.report}: {exc}") from None
    previews = None
    if args.previews:
        from .report import previews as previews_md

        try:
            previews = previews_md.load(args.previews)
        except (OSError, ValueError) as exc:
            raise UsageError(f"cannot read previews {args.previews}: {exc}") from None
    ctx = comment.Context.from_env(pr=args.pr)
    try:
        result = comment.post(report, ctx, previews=previews)
    except comment.Skip as exc:
        sys.stdout.write(f"usd-asset-gate comment: skipped: {exc}\n")
        return EXIT_PASS
    except comment.CommentError as exc:
        raise UsageError(f"comment: {exc}") from None
    sys.stdout.write(f"usd-asset-gate comment: {result} on #{ctx.pr_number}\n")
    return EXIT_PASS


def preview_changes(args: argparse.Namespace, config):
    """The repository top and (display path, Change) for every changed USD file to preview."""
    from .discovery import _git, display_path, filter_paths, under_any
    from .preview.run import changes_since

    try:
        _git(["rev-parse", "--verify", "--quiet", f"{args.changed_since}^{{commit}}"])
        top = os.fsdecode(_git(["rev-parse", "--show-toplevel"]).rstrip(b"\n"))
        changes = changes_since(args.changed_since, top)
    except DiscoveryError as exc:
        raise UsageError(f"--changed-since {args.changed_since}: {exc}") from None
    for p in args.paths:
        if not os.path.exists(p):
            raise UsageError(f"no such file or directory: {p}")
    rows = []
    for c in changes:
        full = os.path.join(top, c.path)
        shown = display_path(full)
        if args.paths and not under_any(full, args.paths):
            continue
        if filter_paths([shown], config.include, config.exclude):
            rows.append((shown, c))
    return top, rows


def cmd_preview(args: argparse.Namespace) -> int:
    require_pxr()
    try:
        import numpy  # noqa: F401
    except ImportError:
        raise UsageError("preview needs numpy: pip install 'usd-asset-gate[preview]'") from None
    from .preview import run as preview_run
    from .preview.strata import Renderer
    from .report import previews as previews_md

    config = load_config(args.config)
    if not config.preview.get("enabled", False) and not args.force:
        sys.stdout.write("usd-asset-gate preview: skipped (preview.enabled is not true; pass --force)\n")
        return EXIT_PASS
    settings = preview_run.Settings.from_config(config.preview)
    if not args.changed_since:
        if not args.paths:
            raise UsageError("no paths given (pass files or directories, or --changed-since REF)")
        files = [(p, "added", None, p) for p in discover(args.paths, config)]
        doc = preview_run.run(files, args.out, settings, Renderer)
    else:
        top, rows = preview_changes(args, config)
        if not any(c.kind != "added" for _, c in rows):  # nothing needs the base
            files = [(shown, c.kind, None, os.path.join(top, c.path)) for shown, c in rows]
            doc = preview_run.run(files, args.out, settings, Renderer)
        else:
            try:
                with preview_run.BaseWorktree(args.changed_since, top) as base:
                    files = [
                        (
                            shown,
                            c.kind,
                            None if c.kind == "added" else os.path.join(base, c.path),
                            None if c.kind == "deleted" else os.path.join(top, c.path),
                        )
                        for shown, c in rows
                    ]
                    doc = preview_run.run(files, args.out, settings, Renderer)
            except DiscoveryError as exc:
                raise UsageError(f"--changed-since {args.changed_since}: {exc}") from None
    if args.json_file:
        preview_run.write_json(doc, args.json_file)
    section = previews_md.render(doc)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(section + "\n")
    sys.stdout.write(section)
    return EXIT_PASS


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = {"check": cmd_check, "rules": cmd_rules, "comment": cmd_comment, "preview": cmd_preview}[
        args.command
    ]
    try:
        return handler(args)
    except UsageError as exc:
        sys.stderr.write(f"usd-asset-gate: error: {exc}\n")
        return EXIT_USAGE
