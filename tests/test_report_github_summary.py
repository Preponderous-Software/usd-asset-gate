"""Golden tests for GitHub annotations and the Markdown job summary; line numbers."""

from __future__ import annotations

import json

from conftest import DEFAULT_CONFIG, FAIL_DIR
from usd_asset_gate.model import Finding, Report, Severity
from usd_asset_gate.report import github, summary
from usd_asset_gate.stage import usda_prim_line

E = Finding(
    rule="unresolved-references",
    code="UnresolvableDependency",
    severity=Severity.ERROR,
    file="props/crate.usda",
    message="reference 'props/textures/base.usda' cannot be resolved",
    prim_path="/Crate",
    line=8,
)
W = Finding(
    rule="prim-budget",
    code="PrimBudgetExceeded",
    severity=Severity.WARNING,
    file="props/crate.usda",
    message="12000 prims; the budget is 5000",
)
I = Finding(
    rule="prim-naming",
    code="PrimNamingFindingsCapped",
    severity=Severity.INFO,
    file="sets/yard.usda",
    message="3 more prim name(s) do not match",
    fixer_available=True,
)
REPORT = Report(files=["props/crate.usda", "sets/yard.usda", "ok.usda"], findings=[E, W, I])


def test_escaping():
    assert github.escape_data("50% done\r\nnext") == "50%25 done%0D%0Anext"
    assert github.escape_property("a:b,c%d\ne") == "a%3Ab%2Cc%25d%0Ae"


def test_annotations_golden():
    assert github.render(REPORT) == (
        "::error file=props/crate.usda,line=8,title=usd-asset-gate%3A unresolved-references::"
        "</Crate> reference 'props/textures/base.usda' cannot be resolved [UnresolvableDependency]\n"
        "::warning file=props/crate.usda,title=usd-asset-gate%3A prim-budget::"
        "12000 prims; the budget is 5000 [PrimBudgetExceeded]\n"
        "::notice file=sets/yard.usda,title=usd-asset-gate%3A prim-naming::"
        "3 more prim name(s) do not match [PrimNamingFindingsCapped] "
        "(an OpenUSD fixer exists; not applied)\n"
    )


def test_annotation_properties_are_escaped():
    f = Finding(rule="r", code="C", severity=Severity.ERROR, file="a,b:c.usda", message="x\ny")
    assert github.annotation(f) == "::error file=a%2Cb%3Ac.usda,title=usd-asset-gate%3A r::x%0Ay [C]"


def test_most_severe_first_and_overflow_notice():
    infos = [
        Finding(rule="r", code="C", severity=Severity.INFO, file=f"{i}.usda", message="m")
        for i in range(3)
    ]
    errors = [
        Finding(rule="r", code="C", severity=Severity.ERROR, file=f"z{i}.usda", message="m")
        for i in range(3)
    ]
    out = github.render(Report(files=[], findings=infos + errors), max_per_severity=2).splitlines()
    assert [line.split(" ", 1)[0] for line in out] == [
        "::error",
        "::error",
        "::notice",
        "::notice",
        "::notice",
    ]
    assert out[-1] == (
        "::notice title=usd-asset-gate::2 more finding(s) are not annotated; "
        "see the job summary and the log for the full list."
    )


def test_summary_golden():
    assert summary.render(REPORT) == (
        "## USD asset gate\n"
        "\n"
        "❌ **FAIL** — 3 USD files checked: 1 error(s), 1 warning(s), 1 info (fail_on: `error`)\n"
        "\n"
        "| File | Errors | Warnings | Info |\n"
        "|---|---:|---:|---:|\n"
        "| `props/crate.usda` | 1 | 1 | 0 |\n"
        "| `sets/yard.usda` | 0 | 0 | 1 |\n"
        "\n"
        "1 file with no findings.\n"
        "\n"
        "<details open><summary>Findings (3)</summary>\n"
        "\n"
        "| Severity | File | Where | Rule | Message |\n"
        "|---|---|---|---|---|\n"
        "| 🔴 error | `props/crate.usda` | line 8 `/Crate` | `unresolved-references` | "
        "reference 'props/textures/base.usda' cannot be resolved (`UnresolvableDependency`) |\n"
        "| 🟡 warning | `props/crate.usda` |  | `prim-budget` | "
        "12000 prims; the budget is 5000 (`PrimBudgetExceeded`) |\n"
        "| 🔵 info | `sets/yard.usda` |  | `prim-naming` | "
        "3 more prim name(s) do not match (`PrimNamingFindingsCapped`) "
        "— an OpenUSD fixer exists (not applied) |\n"
        "\n"
        "</details>\n"
    )


def test_summary_pass():
    text = summary.render(Report(files=["a.usda"], findings=[]))
    assert "✅ **PASS** — 1 USD file checked" in text
    assert "<details" not in text


def test_summary_cells_escape_pipes_and_newlines():
    f = Finding(rule="r", code="C", severity=Severity.ERROR, file="a.usda", message="x | y\nz <b>")
    text = summary.render(Report(files=["a.usda"], findings=[f]))
    assert "x \\| y z &lt;b&gt;" in text


def test_summary_truncates_under_byte_budget():
    many = [
        Finding(rule="r", code="C", severity=Severity.ERROR, file="a.usda", message="m" * 200 + str(i))
        for i in range(500)
    ]
    report = Report(files=["a.usda"], findings=many)
    text = summary.render(report, max_bytes=20_000)
    assert len(text.encode("utf-8")) <= 20_000
    assert "more finding(s) not shown" in text
    assert text.rstrip().endswith("</details>")
    # The full default budget keeps everything here.
    assert "not shown" not in summary.render(report)


def test_summary_is_appended(tmp_path, cli):
    target = tmp_path / "step_summary.md"
    target.write_text("previous step\n")
    code, _, _ = cli(
        "check", "--config", str(DEFAULT_CONFIG), "--summary", str(target),
        str(FAIL_DIR / "no_default_prim"),
    )
    assert code == 1
    text = target.read_text()
    assert text.startswith("previous step\n## USD asset gate")
    assert "`default-prim`" in text


def test_json_file_written_alongside_github_format(tmp_path, cli):
    out_json = tmp_path / "report.json"
    code, out, _ = cli(
        "check", "--config", str(DEFAULT_CONFIG), "--format", "github", "--json", str(out_json),
        str(FAIL_DIR / "missing_reference"),
    )
    assert code == 1
    first = out.splitlines()[0]
    assert first.startswith("::error file=tests/fixtures/fail/missing_reference/asset.usda,line=8,")
    lines = out.splitlines()
    # The human-readable report sits inside a stop-commands guard with a random token.
    guard = next(i for i, line in enumerate(lines) if line.startswith("::stop-commands::"))
    token = lines[guard].split("::")[2]
    assert len(token) == 32
    assert lines[-1] == f"::{token}::"
    assert lines[-2].startswith("FAIL: 1 file checked")
    data = json.loads(out_json.read_text())
    assert data["findings"][0]["line"] == 8


# --- best-effort usda line numbers ------------------------------------------------

USDA = '''#usda 1.0
(
    defaultPrim = "World"
)

def Xform "World"
{
    def Scope "Looks"
    {
        def Material "wood"
        {
        }
    }

    over "Props"
    {
        class "wood"
        {
        }
    }
}
'''


def test_usda_prim_line():
    assert usda_prim_line(USDA, "/World") == 6
    assert usda_prim_line(USDA, "/World/Looks/wood") == 10
    assert usda_prim_line(USDA, "/World/Props/wood") == 17
    assert usda_prim_line(USDA, "/World/Looks/wood.inputs:x") == 10
    assert usda_prim_line(USDA, "/Nope") is None
    assert usda_prim_line(USDA, "/") is None


def test_guard_neutralises_forged_commands_in_file_names():
    forged = Finding(
        rule="r", code="C", severity=Severity.ERROR,
        file="a\n::error::forged\n::stop-commands::x.usda", message="m",
    )
    from usd_asset_gate.report import text

    out = github.guarded(text.render(Report(files=[forged.file], findings=[forged])), token="T0K")
    lines = out.splitlines()
    assert lines[0] == "::stop-commands::T0K"
    assert lines[-1] == "::T0K::"
    # Every forged command line is inside the guard.
    inside = lines[1:-1]
    assert "::error::forged" in inside and "::stop-commands::x.usda: error r: m [C]" in inside
    # The annotation itself escapes the newline, so it stays one command.
    assert "\n" not in github.annotation(forged)
    assert github.guarded("x").endswith("\n") and github.guarded("x") != github.guarded("x")


def test_summary_budget_covers_many_distinct_files():
    many = [
        Finding(rule="r", code="C", severity=Severity.ERROR, file=f"assets/{'deep/' * 20}f{i}.usda", message="m")
        for i in range(2000)
    ]
    report = Report(files=[f.file for f in many], findings=many)
    text = summary.render(report, max_bytes=20_000)
    assert len(text.encode("utf-8")) <= 20_000
    assert "more file(s) with findings not shown" in text
    assert "more finding(s)" not in text or text.rstrip().endswith("</details>")
    tiny = summary.render(report, max_bytes=300)
    assert len(tiny.encode("utf-8")) <= 300


# --- summary helpers and render options ------------------------------------------


def test_summary_cell_escapes_backslashes_before_pipes_and_flattens_crlf():
    assert summary.cell("a\\b\r\nc|d") == "a\\\\b  c\\|d"


def test_summary_code_uses_a_double_fence_around_backticks():
    assert summary.code("a.usda") == "`a.usda`"
    assert summary.code("a`b") == "`` a`b ``"
    assert summary.code("x|y\nz") == "`x\\|y z`"


def test_summary_headline_verdict_follows_fail_on_and_pluralises():
    err = Finding(rule="r", code="C", severity=Severity.ERROR, file="a.usda", message="m")
    warn = Finding(rule="r", code="C", severity=Severity.WARNING, file="a.usda", message="m")
    assert summary.headline(Report(files=["a.usda"], findings=[err], fail_on="never")) == (
        "✅ **PASS** — 1 USD file checked: 1 error(s), 0 warning(s), 0 info (fail_on: `never`)"
    )
    assert summary.headline(
        Report(files=["a.usda", "b.usda"], findings=[warn], fail_on="warning")
    ) == "❌ **FAIL** — 2 USD files checked: 0 error(s), 1 warning(s), 0 info (fail_on: `warning`)"


def test_summary_counts_clean_files_in_the_plural():
    text = summary.render(Report(files=["a.usda", "b.usda"], findings=[]))
    assert "2 files with no findings.\n" in text


def test_summary_where_cell_with_a_line_but_no_prim():
    f = Finding(rule="r", code="C", severity=Severity.ERROR, file="a.usda", message="m", line=5)
    text = summary.render(Report(files=["a.usda"], findings=[f]))
    assert "| 🔴 error | `a.usda` | line 5  | `r` | m (`C`) |\n" in text


def test_summary_title_and_overflow_hint_are_used():
    many = [
        Finding(rule="r", code="C", severity=Severity.ERROR, file="a.usda", message="m" * 200 + str(i))
        for i in range(500)
    ]
    text = summary.render(
        Report(files=["a.usda"], findings=many),
        max_bytes=20_000,
        title="### Custom",
        overflow_hint="the log",
    )
    assert text.startswith("### Custom\n\n❌ **FAIL**")
    assert "not shown to stay under the size limit; see the log._" in text
    assert "the JSON report" not in text


def test_summary_falls_back_to_the_headline_when_no_table_fits():
    f = Finding(rule="r", code="C", severity=Severity.ERROR, file="a.usda", message="m")
    report = Report(files=["a.usda"], findings=[f])
    expected = (
        "## USD asset gate\n\n"
        + summary.headline(report)
        + "\n\n_Details omitted to stay under the size limit; "
        "see the JSON report and the job log._\n"
    )
    assert summary.render(report, max_bytes=len(expected.encode("utf-8"))) == expected


def test_summary_fallback_never_splits_a_utf8_character():
    f = Finding(rule="r", code="C", severity=Severity.ERROR, file="a.usda", message="m")
    # 19 bytes of title and blank line, then the 3-byte ❌: a 20-byte budget cuts into it.
    assert summary.render(Report(files=["a.usda"], findings=[f]), max_bytes=20) == (
        "## USD asset gate\n\n"
    )


def test_summary_append_to_adds_a_missing_final_newline_only(tmp_path):
    target = tmp_path / "step_summary.md"
    summary.append_to(str(target), "first")
    summary.append_to(str(target), "second\n")
    assert target.read_text(encoding="utf-8") == "first\nsecond\n"


NESTED = """#usda 1.0
(
    defaultPrim = "A"
    customData = {
        string note = "def Xform \"Child\" { }"
    }
)

def Xform "A" (
    customData = { int x = 1 }
)
{
    def Xform "Group"
    {
        def Xform "Child"
        {
        }
    }

    def Xform "Child"
    {
    }
}
"""


def test_usda_prim_line_respects_hierarchy():
    assert usda_prim_line(NESTED, "/A") == 9
    assert usda_prim_line(NESTED, "/A/Group/Child") == 15
    assert usda_prim_line(NESTED, "/A/Child") == 20
    assert usda_prim_line(NESTED, "/Child") is None
    assert usda_prim_line(NESTED, "/A/Group/Missing") is None
