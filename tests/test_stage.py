"""Stage helpers: native stderr capture, error text, path handling and usda scanning."""

from __future__ import annotations

import os

import pytest

from usd_asset_gate.stage import (
    AssetContext,
    capture_native_stderr,
    open_stage,
    read_usda_text,
    tf_error_messages,
    usda_prim_line,
)


def test_capture_native_stderr_collects_non_blank_lines_on_exit():
    sink = ["earlier"]
    before = os.fstat(2).st_ino
    with capture_native_stderr(sink):
        os.write(2, b"line one\n\n   \nline two\n")
        assert sink == ["earlier"]  # appended only when the block exits
    assert sink == ["earlier", "line one", "line two"]
    assert os.fstat(2).st_ino == before  # fd 2 restored


def test_capture_native_stderr_restores_fd_when_the_block_raises():
    sink: list[str] = []
    before = os.fstat(2).st_ino
    with pytest.raises(RuntimeError):
        with capture_native_stderr(sink):
            os.write(2, b"before the error\n")
            raise RuntimeError("boom")
    assert sink == ["before the error"]
    assert os.fstat(2).st_ino == before


def test_tf_error_messages_fall_back_to_the_whole_text():
    exc = Exception("  not a Tf error\n\tsecond  line \n")
    assert tf_error_messages(exc) == ["not a Tf error second  line"]


def test_relativize_strips_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = AssetContext(path="a.usda", abs_path=os.path.join(os.getcwd(), "a.usda"))
    cwd = os.getcwd()
    assert ctx.relativize(f"Failed to open @{cwd}{os.sep}sub{os.sep}a.usda@") == (
        f"Failed to open @sub{os.sep}a.usda@"
    )
    assert ctx.relativize("@/elsewhere/a.usda@") == "@/elsewhere/a.usda@"


def test_open_stage_success_sets_the_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ok.usda").write_text('#usda 1.0\n\ndef Xform "Root"\n{\n}\n', encoding="utf-8")
    ctx = AssetContext(path="ok.usda", abs_path=str(tmp_path / "ok.usda"))
    assert open_stage(ctx) == []
    assert ctx.stage is not None
    assert ctx.stage.GetPrimAtPath("/Root").IsValid()


def test_open_stage_failure_leaves_no_stage_and_relative_messages(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "broken.usda").write_text('#usda 1.0\n\ndef "Root"\n{\n', encoding="utf-8")
    ctx = AssetContext(path="broken.usda", abs_path=str(tmp_path / "broken.usda"))
    messages = open_stage(ctx)
    assert messages
    assert ctx.stage is None
    cwd = os.getcwd() + os.sep
    assert not any(cwd in m for m in messages)


def test_read_usda_text_only_reads_text_layers(tmp_path):
    usda = tmp_path / "a.usda"
    usda.write_bytes(b'#usda 1.0\ndef "A"\n{\n}\n')
    assert read_usda_text(str(usda)) == '#usda 1.0\ndef "A"\n{\n}\n'

    usdc = tmp_path / "b.usda"  # binary crate content behind a text extension
    usdc.write_bytes(b"PXR-USDC" + b"\0" * 16)
    assert read_usda_text(str(usdc)) is None

    assert read_usda_text(str(tmp_path / "missing.usda")) is None


def test_read_usda_text_replaces_invalid_utf8(tmp_path):
    usda = tmp_path / "a.usda"
    usda.write_bytes(b"#usda 1.0\n# caf\xe9\n")
    assert read_usda_text(str(usda)) == "#usda 1.0\n# caf�\n"


TRICKY = '''#usda 1.0
(
    doc = """def "Fake" {
"""
)

# def "Commented"
def Xform "Root" (
    customData = {
        string note = "}{"
    }
)
{
    string label = "def \\"Nope\\" {"
    asset tex = @./weird{.png@
    def "Child"
    {
    }
}
'''


def test_usda_prim_line_ignores_comments_strings_metadata_and_asset_paths():
    assert usda_prim_line(TRICKY, "/Root") == 8
    assert usda_prim_line(TRICKY, "/Root/Child") == 16
    assert usda_prim_line(TRICKY, "/Fake") is None
    assert usda_prim_line(TRICKY, "/Commented") is None
    assert usda_prim_line(TRICKY, "/Root/Nope") is None
