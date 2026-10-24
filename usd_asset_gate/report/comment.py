"""Create or update one pull-request comment, identified by a hidden marker.

Uses the GitHub REST API through ``urllib.request`` only:

* ``GET  /repos/{owner}/{repo}/issues/{n}/comments`` (paginated via ``Link``) to find
  the comment whose body starts with :data:`MARKER`;
* ``PATCH /repos/{owner}/{repo}/issues/comments/{id}`` to update it, or
* ``POST /repos/{owner}/{repo}/issues/{n}/comments`` to create it.

The step never fails the build for reasons outside the asset check: with no token,
no repository or no pull-request number, or when GitHub answers 401/403 (a pull
request from a fork gets a read-only ``GITHUB_TOKEN``), it logs why and returns
without posting. The log, annotations and job summary still carry the results.
``pull_request_target`` is never needed or used.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .. import __version__
from ..model import Report
from . import summary

MARKER = "<!-- usd-asset-gate:report -->"
TITLE = "## USD asset gate"
#: GitHub rejects comment bodies over 65,536 characters; stay well under it.
MAX_BODY_BYTES = 60_000

_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class CommentError(Exception):
    """An unexpected API failure (not a permissions skip)."""


class Skip(Exception):
    """The comment step cannot or should not post; the message says why."""


@dataclass
class Context:
    token: str | None
    repository: str | None
    pr_number: int | None
    api_url: str = "https://api.github.com"
    run_url: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, pr: int | None = None) -> "Context":
        env = dict(os.environ if env is None else env)
        number = pr
        event_path = env.get("GITHUB_EVENT_PATH")
        if number is None and event_path and os.path.isfile(event_path):
            try:
                with open(event_path, encoding="utf-8") as fh:
                    event = json.load(fh)
            except (OSError, ValueError):
                event = {}
            pull = event.get("pull_request") or {}
            number = pull.get("number")
            if number is None and (event.get("issue") or {}).get("pull_request"):
                number = event["issue"].get("number")
        run_url = None
        if env.get("GITHUB_RUN_ID") and env.get("GITHUB_REPOSITORY"):
            server = env.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
            run_url = f"{server}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"
        return cls(
            token=env.get("GITHUB_TOKEN") or None,
            repository=env.get("GITHUB_REPOSITORY") or None,
            pr_number=int(number) if number is not None else None,
            api_url=(env.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/"),
            run_url=run_url,
        )


def render_body(report: Report, run_url: str | None = None, previews: dict | None = None) -> str:
    """The comment text: the marker, then either an all-pass note or the summary, then the
    advisory "Visual changes" section when a ``preview --json`` document is given."""
    body = _render_checks(report, run_url)
    if previews is not None:
        from . import previews as previews_md

        section = previews_md.render(previews, run_url)
        if len(body.encode("utf-8")) + len(section.encode("utf-8")) + 2 <= MAX_BODY_BYTES:
            body += "\n" + section
    return body


def _render_checks(report: Report, run_url: str | None) -> str:
    if not report.findings:
        n = len(report.files)
        note = f"✅ **All checks pass** — {n} USD file{'s' if n != 1 else ''} checked"
        note += f" (fail_on: `{report.fail_on}`)."
        if run_url:
            note += f" [Workflow run]({run_url})"
        return f"{MARKER}\n{TITLE}\n\n{note}\n"
    hint = f"the [job summary]({run_url})" if run_url else "the job summary"
    body = summary.render(
        report,
        max_bytes=MAX_BODY_BYTES - len(MARKER) - 200,
        title=TITLE,
        overflow_hint=hint,
    )
    footer = f"\n_usd-asset-gate {__version__}"
    footer += f" · [workflow run]({run_url})_\n" if run_url else "_\n"
    return f"{MARKER}\n{body}{footer}"


Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, str], Any]]


def urllib_transport(method: str, url: str, headers: dict[str, str], data: bytes | None):
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = raw.decode("utf-8", errors="replace")
        return exc.code, dict(exc.headers or {}), payload
    except urllib.error.URLError as exc:
        raise CommentError(f"cannot reach {url}: {exc.reason}") from None


class Client:
    def __init__(self, ctx: Context, transport: Transport = urllib_transport):
        self.ctx = ctx
        self.transport = transport

    def _call(self, method: str, url: str, body: dict | None = None):
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.ctx.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"usd-asset-gate/{__version__}",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, resp_headers, payload = self.transport(method, url, headers, data)
        if status in (401, 403):
            message = payload.get("message") if isinstance(payload, dict) else payload
            raise Skip(
                f"GitHub answered {status} to {method} ({message}); the token cannot write "
                "comments here (for example on a pull request from a fork)"
            )
        if status >= 300:
            raise CommentError(f"{method} {url} failed with HTTP {status}: {payload}")
        return resp_headers, payload

    def find_existing(self) -> dict | None:
        url = (
            f"{self.ctx.api_url}/repos/{self.ctx.repository}/issues/{self.ctx.pr_number}"
            "/comments?per_page=100"
        )
        while url:
            headers, page = self._call("GET", url)
            for comment in page or []:
                if (comment.get("body") or "").startswith(MARKER):
                    return comment
            link = {k.lower(): v for k, v in headers.items()}.get("link", "")
            m = _NEXT_RE.search(link)
            url = m.group(1) if m else None
        return None

    def upsert(self, body: str, create_if_missing: bool = True) -> str:
        existing = self.find_existing()
        if existing is not None:
            url = f"{self.ctx.api_url}/repos/{self.ctx.repository}/issues/comments/{existing['id']}"
            self._call("PATCH", url, {"body": body})
            return f"updated comment {existing['id']}"
        if not create_if_missing:
            return "no existing comment and nothing to report; nothing posted"
        url = f"{self.ctx.api_url}/repos/{self.ctx.repository}/issues/{self.ctx.pr_number}/comments"
        _, created = self._call("POST", url, {"body": body})
        return f"created comment {created.get('id') if isinstance(created, dict) else '?'}"


def post(
    report: Report, ctx: Context, transport: Transport = urllib_transport, previews: dict | None = None
) -> str:
    """Create or update the comment; returns a log line. Raises Skip or CommentError."""
    if not ctx.token:
        raise Skip("GITHUB_TOKEN is not set")
    if not ctx.repository:
        raise Skip("GITHUB_REPOSITORY is not set")
    if ctx.pr_number is None:
        raise Skip("no pull request number (not a pull_request event and no --pr)")
    body = render_body(report, ctx.run_url, previews)
    # A run that checked no USD files only edits an existing comment; it never
    # starts a new one, so pull requests without assets get no bot comment.
    has_previews = bool(previews and previews.get("files"))
    return Client(ctx, transport).upsert(body, create_if_missing=bool(report.files) or has_previews)
