"""PR comment create-or-update against a fake GitHub API (http.server in a thread)."""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import run_cli
from usd_asset_gate import __version__
from usd_asset_gate.model import Finding, Report, Severity
from usd_asset_gate.report import comment
from usd_asset_gate.report import json as json_report
from usd_asset_gate.report import previews as previews_report

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


# --- context from the environment ------------------------------------------------


def test_explicit_pr_beats_the_event(tmp_path):
    c = comment.Context.from_env({"GITHUB_EVENT_PATH": write_event(tmp_path, number=7)}, pr=3)
    assert c.pr_number == 3


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "",
        json.dumps({"ref": "refs/heads/main"}),
        json.dumps({"pull_request": None}),
        json.dumps({"issue": {"number": 9}}),  # a comment on a plain issue, not a pull request
        json.dumps({"issue": {"number": 9, "pull_request": None}}),
    ],
)
def test_event_without_a_pull_request_gives_no_number(tmp_path, content):
    event = tmp_path / "e.json"
    event.write_text(content)
    assert comment.Context.from_env({"GITHUB_EVENT_PATH": str(event)}).pr_number is None


def test_missing_event_file_gives_no_number(tmp_path):
    env = {"GITHUB_EVENT_PATH": str(tmp_path / "absent.json")}
    assert comment.Context.from_env(env).pr_number is None


def test_context_strips_trailing_slashes_from_enterprise_urls():
    c = comment.Context.from_env(
        {
            "GITHUB_API_URL": "https://ghe.example/api/v3/",
            "GITHUB_SERVER_URL": "https://ghe.example/",
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_RUN_ID": "5",
        }
    )
    assert c.api_url == "https://ghe.example/api/v3"
    assert c.run_url == "https://ghe.example/o/r/actions/runs/5"


def test_empty_variables_count_as_unset():
    c = comment.Context.from_env({"GITHUB_TOKEN": "", "GITHUB_REPOSITORY": "", "GITHUB_API_URL": ""})
    assert c.token is None and c.repository is None
    assert c.api_url == "https://api.github.com"


@pytest.mark.parametrize("env", [{"GITHUB_REPOSITORY": "o/r"}, {"GITHUB_RUN_ID": "5"}])
def test_run_url_needs_both_the_repository_and_the_run_id(env):
    assert comment.Context.from_env(env).run_url is None


def test_context_defaults_to_the_process_environment(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "from-the-environment")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    c = comment.Context.from_env(pr=1)
    assert (c.token, c.repository) == ("from-the-environment", "o/r")


# --- rendering -------------------------------------------------------------------


def test_all_pass_body_is_exact():
    report = Report(files=["a.usda"], findings=[], fail_on="warning")
    assert comment.render_body(report, "https://x/run") == (
        f"{comment.MARKER}\n## USD asset gate\n\n"
        "✅ **All checks pass** — 1 USD file checked (fail_on: `warning`). "
        "[Workflow run](https://x/run)\n"
    )


@pytest.mark.parametrize(
    "files, phrase", [([], "0 USD files checked"), (["a.usda", "b.usda"], "2 USD files checked")]
)
def test_all_pass_body_pluralises_files(files, phrase):
    assert phrase in comment.render_body(Report(files=files, findings=[]))


def test_failing_body_without_a_run_url_has_a_plain_footer():
    body = comment.render_body(failing_report())
    assert body.endswith(f"\n_usd-asset-gate {__version__}_\n")
    assert "workflow run" not in body.lower()


def test_truncated_body_without_a_run_url_points_at_the_job_summary():
    many = [
        Finding(rule="r", code="C", severity=Severity.ERROR, file="a.usda", message="m" * 300 + str(i))
        for i in range(1000)
    ]
    body = comment.render_body(Report(files=["a.usda"], findings=many))
    assert "not shown to stay under the size limit; see the job summary._" in body


def _unavailable(message_bytes: int) -> dict:
    return {"status": "unavailable", "message": "x" * message_bytes, "files": []}


def test_previews_section_is_added_only_while_the_body_fits():
    report = passing_report()
    base = comment.render_body(report)
    overhead = len(previews_report.render(_unavailable(0)).encode("utf-8"))
    room = comment.MAX_BODY_BYTES - len(base.encode("utf-8")) - overhead - 2

    fits = comment.render_body(report, None, _unavailable(room))
    assert previews_report.HEADING in fits
    assert len(fits.encode("utf-8")) == comment.MAX_BODY_BYTES - 1

    assert comment.render_body(report, None, _unavailable(room + 1)) == base


def test_previews_with_no_files_do_not_start_a_comment(api):
    result = comment.post(Report(files=[], findings=[]), ctx(api), previews={"files": []})
    assert "nothing posted" in result
    assert api.comments == {}


# --- finding the comment ---------------------------------------------------------


def test_marker_must_open_the_comment(api):
    quoted = f"quoting the bot: {comment.MARKER}"
    cid = api.add(quoted)
    assert comment.post(failing_report(), ctx(api)).startswith("created comment")
    assert api.comments[cid]["body"] == quoted
    assert len(api.marked()) == 1


def test_comment_with_a_null_body_is_passed_over(api):
    api.add(None)
    assert comment.post(failing_report(), ctx(api)).startswith("created comment")
    assert len(api.comments) == 2


def test_the_oldest_marked_comment_is_the_one_edited(api):
    first = api.add(comment.MARKER + "\nfirst")
    second = api.add(comment.MARKER + "\nsecond")
    assert comment.post(passing_report(), ctx(api)) == f"updated comment {first}"
    assert api.comments[second]["body"] == comment.MARKER + "\nsecond"


# --- client, with a stub transport -----------------------------------------------

FIRST_PAGE = f"https://api.test/repos/{REPO}/issues/{PR}/comments?per_page=100"


class StubTransport:
    """Answers each call from a list of (status, headers, payload), recording the calls."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, headers, data):
        self.calls.append((method, url, headers, data))
        return self.answers.pop(0)


def client(transport: StubTransport) -> comment.Client:
    context = comment.Context(token=TOKEN, repository=REPO, pr_number=PR, api_url="https://api.test")
    return comment.Client(context, transport)


def test_requests_carry_the_api_headers_and_a_json_body():
    stub = StubTransport((200, {}, []), (201, {}, {"id": 1}))
    assert client(stub).upsert("hello") == "created comment 1"
    (_, _, get_headers, get_data), (_, _, post_headers, post_data) = stub.calls
    expected = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"usd-asset-gate/{__version__}",
    }
    assert get_headers == expected and get_data is None
    assert post_headers == {**expected, "Content-Type": "application/json"}
    assert json.loads(post_data) == {"body": "hello"}


@pytest.mark.parametrize(
    "link, urls",
    [
        ('<https://api.test/p2>; rel="next"', [FIRST_PAGE, "https://api.test/p2"]),
        (
            '<https://api.test/p1>; rel="prev", <https://api.test/p3>; rel="next"',
            [FIRST_PAGE, "https://api.test/p3"],
        ),
        ('<https://api.test/p1>; rel="prev"', [FIRST_PAGE]),
    ],
)
def test_pagination_follows_only_rel_next_in_a_lowercase_header(link, urls):
    stub = StubTransport((200, {"link": link}, []), (200, {}, []))
    assert client(stub).find_existing() is None
    assert [call[1] for call in stub.calls] == urls


def test_create_tolerates_empty_answers():
    stub = StubTransport((200, {}, None), (201, {}, None))
    assert client(stub).upsert("hello") == "created comment ?"


@pytest.mark.parametrize(
    "payload, shown",
    [({"message": "Bad credentials"}, "Bad credentials"), ("Bad credentials", "Bad credentials"), (None, "None")],
)
def test_401_is_a_skip_quoting_the_answer(payload, shown):
    with pytest.raises(comment.Skip, match=rf"401 to GET \({shown}\)"):
        client(StubTransport((401, {}, payload))).find_existing()


@pytest.mark.parametrize("status", [302, 422, 500])
def test_other_statuses_are_comment_errors(status):
    with pytest.raises(comment.CommentError) as raised:
        client(StubTransport((status, {}, {"message": "nope"}))).find_existing()
    assert str(raised.value) == f"GET {FIRST_PAGE} failed with HTTP {status}: {{'message': 'nope'}}"


# --- urllib transport ------------------------------------------------------------


@pytest.fixture
def canned():
    """A server answering every GET with ``answer``; yields (answer, url)."""
    answer = {"status": 200, "body": b""}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(answer["status"])
            self.send_header("Content-Length", str(len(answer["body"])))
            self.end_headers()
            self.wfile.write(answer["body"])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield answer, f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize(
    "status, raw, payload",
    [
        (200, b"", None),
        (200, b'{"id": 1}', {"id": 1}),
        (404, b'{"message": "Not Found"}', {"message": "Not Found"}),
        (500, b"", None),
        (502, b"<html>bad gateway</html>", "<html>bad gateway</html>"),
        (502, b"\xff", "�"),
    ],
)
def test_urllib_transport_decodes_answers_and_error_bodies(canned, status, raw, payload):
    answer, url = canned
    answer.update(status=status, body=raw)
    got_status, headers, got_payload = comment.urllib_transport("GET", url, {}, None)
    assert (got_status, got_payload) == (status, payload)
    assert headers["Content-Length"] == str(len(raw))


@pytest.mark.parametrize("raw", [b"<html>sign in</html>", b"\xff"])
def test_urllib_transport_reports_a_success_body_that_is_not_json(canned, raw):
    answer, url = canned
    answer.update(status=200, body=raw)
    with pytest.raises(comment.CommentError) as raised:
        comment.urllib_transport("GET", url, {}, None)
    assert str(raised.value) == f"GET {url} answered HTTP 200 with a body that is not JSON"


def test_success_body_that_is_not_json_exits_2(canned, cli, tmp_path, monkeypatch):
    answer, url = canned
    answer.update(status=200, body=b"<html>sign in</html>")
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_API_URL", url.rstrip("/"))
    code, _, err = cli("comment", "--from", write_report(tmp_path, failing_report()), "--pr", str(PR))
    assert code == 2
    assert "with a body that is not JSON" in err


def _refuse_connections(monkeypatch):
    def refuse(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(comment.urllib.request, "urlopen", refuse)


def test_urllib_transport_reports_an_unreachable_api(monkeypatch):
    _refuse_connections(monkeypatch)
    with pytest.raises(comment.CommentError) as raised:
        comment.urllib_transport("GET", "https://api.test/x", {}, None)
    assert str(raised.value) == "cannot reach https://api.test/x: connection refused"


def test_unreachable_api_exits_2(cli, tmp_path, monkeypatch):
    _refuse_connections(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_API_URL", "https://api.test")
    code, _, err = cli("comment", "--from", write_report(tmp_path, failing_report()), "--pr", str(PR))
    assert code == 2
    assert "cannot reach https://api.test/" in err


@pytest.fixture
def hangs_up():
    """A raw socket that reads one request, sends ``reply`` and closes; yields (answer, url)."""
    answer = {"reply": b""}
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(5)

    def serve():
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        with conn:
            conn.recv(65536)
            conn.sendall(answer["reply"])

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield answer, f"http://127.0.0.1:{listener.getsockname()[1]}/"
    thread.join()
    listener.close()


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(b"", id="closed-without-an-answer"),
        pytest.param(b"not http\r\n\r\n", id="not-http"),
        pytest.param(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{", id="truncated-success"),
        pytest.param(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 100\r\n\r\n{", id="truncated-error"),
    ],
)
def test_urllib_transport_reports_a_lost_connection(hangs_up, reply):
    answer, url = hangs_up
    answer["reply"] = reply
    with pytest.raises(comment.CommentError) as raised:
        comment.urllib_transport("GET", url, {}, None)
    message = str(raised.value)
    assert message.startswith(f"lost the connection to {url}: ")
    assert message == message.strip()


def test_urllib_transport_reports_a_read_timeout(monkeypatch):
    def time_out(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(comment.urllib.request, "urlopen", time_out)
    with pytest.raises(comment.CommentError) as raised:
        comment.urllib_transport("GET", "https://api.test/x", {}, None)
    assert str(raised.value) == "lost the connection to https://api.test/x: timed out"


def test_lost_connection_exits_2(hangs_up, cli, tmp_path, monkeypatch):
    _, url = hangs_up
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_API_URL", url.rstrip("/"))
    code, _, err = cli("comment", "--from", write_report(tmp_path, failing_report()), "--pr", str(PR))
    assert code == 2
    assert "lost the connection to " in err
