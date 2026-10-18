"""PR comment create-or-update against a fake GitHub API (http.server in a thread)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import run_cli
from usd_asset_gate.model import Finding, Report, Severity
from usd_asset_gate.report import comment
from usd_asset_gate.report import json as json_report

REPO = "octo/assets"
PR = 7
TOKEN = "test-token"


class FakeGitHub:
    """Just enough of the issues-comments API, with pagination and a 403 switch."""

    def __init__(self, page_size: int = 2):
        self.comments: dict[int, dict] = {}
        self.next_id = 100
        self.page_size = page_size
        self.forbid_writes = False
        self.forbid_all = False
        self.requests: list[tuple[str, str]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def _send(self, status, payload=None, headers=None):
                body = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth_ok(self):
                return self.headers.get("Authorization") == f"Bearer {TOKEN}"

            def do_GET(self):
                fake.requests.append(("GET", self.path))
                if fake.forbid_all or not self._auth_ok():
                    return self._send(403, {"message": "Resource not accessible by integration"})
                base, _, query = self.path.partition("?")
                if base != f"/repos/{REPO}/issues/{PR}/comments":
                    return self._send(404, {"message": "Not Found"})
                params = dict(p.split("=") for p in query.split("&") if p)
                page = int(params.get("page", 1))
                items = sorted(fake.comments.values(), key=lambda c: c["id"])
                chunk = items[(page - 1) * fake.page_size : page * fake.page_size]
                headers = {}
                if page * fake.page_size < len(items):
                    nxt = f"{fake.url}{base}?per_page=100&page={page + 1}"
                    headers["Link"] = f'<{nxt}>; rel="next"'
                return self._send(200, chunk, headers)

            def _body(self):
                length = int(self.headers.get("Content-Length", 0))
                return json.loads(self.rfile.read(length) or b"{}")

            def do_POST(self):
                fake.requests.append(("POST", self.path))
                if fake.forbid_writes or fake.forbid_all:
                    return self._send(403, {"message": "Resource not accessible by integration"})
                if self.path != f"/repos/{REPO}/issues/{PR}/comments":
                    return self._send(404, {"message": "Not Found"})
                cid = fake.next_id
                fake.next_id += 1
                fake.comments[cid] = {"id": cid, "body": self._body()["body"]}
                return self._send(201, fake.comments[cid])

            def do_PATCH(self):
                fake.requests.append(("PATCH", self.path))
                if fake.forbid_writes or fake.forbid_all:
                    return self._send(403, {"message": "Resource not accessible by integration"})
                cid = int(self.path.rsplit("/", 1)[1])
                if cid not in fake.comments:
                    return self._send(404, {"message": "Not Found"})
                fake.comments[cid]["body"] = self._body()["body"]
                return self._send(200, fake.comments[cid])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def add(self, body: str) -> int:
        cid = self.next_id
        self.next_id += 1
        self.comments[cid] = {"id": cid, "body": body}
        return cid

    def marked(self) -> list[dict]:
        return [c for c in self.comments.values() if c["body"].startswith(comment.MARKER)]


@pytest.fixture
def api():
    fake = FakeGitHub()
    fake.thread.start()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


def failing_report() -> Report:
    return Report(
        files=["props/crate.usda"],
        findings=[
            Finding(
                rule="unresolved-references",
                code="UnresolvableDependency",
                severity=Severity.ERROR,
                file="props/crate.usda",
                message="reference 'props/textures/base.usda' cannot be resolved",
                prim_path="/Crate",
            ),
            Finding(
                rule="prim-budget",
                code="PrimBudgetExceeded",
                severity=Severity.WARNING,
                file="props/crate.usda",
                message="12000 prims; the budget is 5000",
            ),
        ],
    )


def passing_report() -> Report:
    return Report(files=["props/crate.usda"], findings=[])


def ctx(api, **kw) -> comment.Context:
    values = dict(token=TOKEN, repository=REPO, pr_number=PR, api_url=api.url, run_url=None)
    values.update(kw)
    return comment.Context(**values)


def write_event(tmp_path, number=PR) -> str:
    path = tmp_path / "event.json"
    path.write_text(json.dumps({"pull_request": {"number": number}}))
    return str(path)


def cli_env(api, tmp_path, **extra) -> dict:
    env = {
        "GITHUB_TOKEN": TOKEN,
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_API_URL": api.url,
        "GITHUB_EVENT_PATH": write_event(tmp_path),
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": "42",
    }
    env.update(extra)
    return env


def write_report(tmp_path, report: Report, name="report.json") -> str:
    path = tmp_path / name
    path.write_text(json_report.render(report))
    return str(path)


# --- create / update -------------------------------------------------------------


def test_running_twice_leaves_one_comment_reflecting_the_second_run(api, tmp_path):
    api.add("an unrelated human comment")
    first = write_report(tmp_path, failing_report(), "first.json")
    proc = run_cli("comment", "--from", first, env=cli_env(api, tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert "created comment" in proc.stdout
    (only,) = api.marked()
    assert "PrimBudgetExceeded" in only["body"]

    second = write_report(tmp_path, passing_report(), "second.json")
    proc = run_cli("comment", "--from", second, env=cli_env(api, tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert f"updated comment {only['id']}" in proc.stdout
    (only_again,) = api.marked()
    assert only_again["id"] == only["id"]
    assert "All checks pass" in only_again["body"]
    assert "PrimBudgetExceeded" not in only_again["body"]
    assert len(api.comments) == 2  # the human comment is untouched


def test_existing_comment_is_found_across_pages(api):
    for i in range(5):
        api.add(f"chatter {i}")
    cid = api.add(comment.MARKER + "\nold report")
    api.add("more chatter")
    result = comment.post(failing_report(), ctx(api))
    assert result == f"updated comment {cid}"
    assert len(api.marked()) == 1
    assert sum(1 for m, _ in api.requests if m == "GET") >= 3  # followed rel="next"


def test_all_clear_edits_instead_of_deleting(api):
    cid = api.add(comment.MARKER + "\nold failing report")
    comment.post(passing_report(), ctx(api))
    assert api.comments[cid]["body"].startswith(comment.MARKER)
    assert "All checks pass" in api.comments[cid]["body"]


def test_no_usd_files_never_starts_a_comment(api):
    assert "nothing posted" in comment.post(Report(files=[], findings=[]), ctx(api))
    assert api.comments == {}


def test_no_usd_files_still_updates_an_existing_comment(api):
    cid = api.add(comment.MARKER + "\nold failing report")
    comment.post(Report(files=[], findings=[]), ctx(api))
    assert "All checks pass" in api.comments[cid]["body"]


def test_body_contents_and_run_link():
    body = comment.render_body(failing_report(), "https://github.com/o/r/actions/runs/1")
    assert body.startswith(comment.MARKER + "\n## USD asset gate\n")
    assert "❌ **FAIL**" in body
    assert "`unresolved-references`" in body
    assert "[workflow run](https://github.com/o/r/actions/runs/1)" in body


def test_long_bodies_are_truncated_with_a_link():
    many = [
        Finding(rule="r", code="C", severity=Severity.ERROR, file="a.usda", message="m" * 300 + str(i))
        for i in range(1000)
    ]
    body = comment.render_body(Report(files=["a.usda"], findings=many), "https://x/run")
    assert len(body) < 65_536
    assert "not shown to stay under the size limit; see the [job summary](https://x/run)" in body


# --- skips -----------------------------------------------------------------------


def test_403_logs_a_skip_and_exits_0(api, tmp_path):
    api.forbid_writes = True
    report = write_report(tmp_path, failing_report())
    proc = run_cli("comment", "--from", report, env=cli_env(api, tmp_path))
    assert proc.returncode == 0
    assert "skipped" in proc.stdout and "403" in proc.stdout
    assert api.comments == {}


def test_403_on_read_also_skips(api):
    api.forbid_all = True
    with pytest.raises(comment.Skip, match="403"):
        comment.post(failing_report(), ctx(api))


@pytest.mark.parametrize(
    "missing, reason",
    [
        ({"GITHUB_TOKEN": ""}, "GITHUB_TOKEN is not set"),
        ({"GITHUB_REPOSITORY": ""}, "GITHUB_REPOSITORY is not set"),
        ({"GITHUB_EVENT_PATH": ""}, "no pull request number"),
    ],
)
def test_missing_context_skips(api, tmp_path, missing, reason):
    report = write_report(tmp_path, failing_report())
    proc = run_cli("comment", "--from", report, env=cli_env(api, tmp_path, **missing))
    assert proc.returncode == 0
    assert reason in proc.stdout
    assert api.requests == []


def test_pr_flag_overrides_the_event(api, tmp_path):
    report = write_report(tmp_path, failing_report())
    env = cli_env(api, tmp_path, GITHUB_EVENT_PATH="")
    proc = run_cli("comment", "--from", report, "--pr", str(PR), env=env)
    assert proc.returncode == 0
    assert len(api.marked()) == 1


def test_unexpected_http_error_exits_2(api, tmp_path):
    report = write_report(tmp_path, failing_report())
    env = cli_env(api, tmp_path, GITHUB_REPOSITORY="octo/elsewhere")  # fake answers 404
    proc = run_cli("comment", "--from", report, env=env)
    assert proc.returncode == 2
    assert "HTTP 404" in proc.stderr


def test_unreadable_report_exits_2(tmp_path):
    bad = tmp_path / "r.json"
    bad.write_text("{not json")
    proc = run_cli("comment", "--from", str(bad))
    assert proc.returncode == 2


def test_context_from_issue_comment_event(tmp_path):
    event = tmp_path / "e.json"
    event.write_text(json.dumps({"issue": {"number": 9, "pull_request": {"url": "x"}}}))
    c = comment.Context.from_env(
        {"GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "5"}
    )
    assert c.pr_number == 9
    assert c.run_url == "https://github.com/o/r/actions/runs/5"
    assert c.api_url == "https://api.github.com"


def test_comment_works_without_pxr(api, tmp_path):
    fake = tmp_path / "shadow" / "pxr"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text("raise ImportError('No module named pxr')\n")
    from conftest import REPO_ROOT

    report = write_report(tmp_path, failing_report())
    env = cli_env(api, tmp_path, PYTHONPATH=f"{tmp_path / 'shadow'}:{REPO_ROOT}")
    proc = run_cli("comment", "--from", report, env=env)
    assert proc.returncode == 0, proc.stderr
    assert len(api.marked()) == 1
