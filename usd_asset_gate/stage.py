"""Open each asset once and share it with every rule through an ``AssetContext``."""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any, Iterator


@contextlib.contextmanager
def capture_native_stderr(sink: list[str]) -> Iterator[None]:
    """Capture what OpenUSD's C++ code writes to file descriptor 2.

    OpenUSD reports many composition problems ("Could not open asset ...",
    "Could not load sublayer ...") as ``TF_WARN`` diagnostics printed to stderr
    instead of raising. They are captured here so they can be attached to the
    report rather than only scrolling past in the log. Lines are appended to
    ``sink`` when the block exits.
    """
    try:
        stderr_fd = 2
        saved = os.dup(stderr_fd)
    except OSError:  # no usable fd 2 (unusual embedding); run uncaptured
        yield
        return
    with tempfile.TemporaryFile(mode="w+b") as tmp:
        try:
            os.dup2(tmp.fileno(), stderr_fd)
            yield
        finally:
            os.dup2(saved, stderr_fd)
            os.close(saved)
            tmp.seek(0)
            text = tmp.read().decode("utf-8", errors="replace")
            sink.extend(line for line in text.splitlines() if line.strip())


_TF_ERROR_RE = re.compile(
    r"Error in '[^']*' at line \d+ in file \S+ : '(.*?)'(?=\s*(?:Error in '|\Z))", re.S
)


def tf_error_messages(exc: BaseException) -> list[str]:
    """Pull the human-readable commentary out of a ``Tf.ErrorException``."""
    messages = [_join_lines(m) for m in _TF_ERROR_RE.findall(str(exc))]
    return messages or [_join_lines(str(exc))]


def _join_lines(text: str) -> str:
    """Join a multi-line message into one line without touching spaces inside a line.

    Collapsing every whitespace run would rewrite paths such as ``my  assets/x.usda``.
    """
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


_STAGE_ADDRESS_RE = re.compile(r" <0x[0-9a-fA-F]+>")


def normalize_diagnostic(line: str) -> str:
    """Drop per-process stage addresses such as ``<0x361c760>`` from native output.

    They differ on every run, which would make reports of an unchanged asset differ.
    """
    return _STAGE_ADDRESS_RE.sub("", line)


@dataclass
class AssetContext:
    """One checked file: its paths, its opened stage and lazily computed data."""

    path: str  # display path, relative to the working directory where possible
    abs_path: str
    stage: Any = None  # pxr.Usd.Stage once opened
    diagnostics: list[str] = field(default_factory=list)
    _stats: dict | None = field(default=None, repr=False)

    @property
    def stats(self) -> dict:
        """``UsdUtils.ComputeUsdStageStats`` for the stage, computed once."""
        if self._stats is None:
            from pxr import UsdUtils

            self._stats = UsdUtils.ComputeUsdStageStats(self.stage)
        return self._stats

    def relativize(self, text: str) -> str:
        """Make absolute paths in OpenUSD messages relative to the working directory.

        Messages then read the same on every machine, which keeps the PR comment
        stable between runs.
        """
        cwd = os.getcwd().rstrip(os.sep) + os.sep
        return text.replace(cwd, "")


def open_stage(ctx: AssetContext) -> list[str]:
    """Open ``ctx.abs_path`` into ``ctx.stage``.

    Returns the error messages when the stage cannot be opened (``ctx.stage``
    stays ``None``); returns an empty list on success. Native diagnostics printed
    while opening are appended to ``ctx.diagnostics``.
    """
    from pxr import Tf, Usd

    try:
        with capture_native_stderr(ctx.diagnostics):
            stage = Usd.Stage.Open(ctx.abs_path)
    except Tf.ErrorException as exc:
        return [ctx.relativize(m) for m in tf_error_messages(exc)]
    if stage is None:
        return [f"Usd.Stage.Open returned no stage for {ctx.path}"]
    ctx.stage = stage
    return []


_PRIM_HEADER_RE = re.compile(r'[ \t]*(?:def|over|class)\b[^"\n]*"([^"\n]+)"')
_TRIPLE_QUOTES = ('"' * 3, "'" * 3)


def _scan_usda(text: str):
    """Prim headers and braces outside strings, comments and metadata parentheses.

    Returns ``(headers, braces)``: ``headers`` is a list of ``(pos, name, depth)``
    for ``def``/``over``/``class`` lines at the start of a line, and ``braces`` a
    list of ``(pos, char, depth_inside)`` for ``{``/``}`` at parenthesis depth 0,
    where ``depth_inside`` is the brace depth of the block the brace opens or closes.
    """
    headers, braces = [], []
    depth = paren = 0
    i, n = 0, len(text)
    line_start = True
    while i < n:
        if line_start and paren == 0:
            m = _PRIM_HEADER_RE.match(text, i)
            if m:
                headers.append((i, m.group(1), depth))
        line_start = False
        c = text[i]
        if c == "\n":
            line_start = True
            i += 1
        elif c == "#":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text[i : i + 3] in _TRIPLE_QUOTES:
            j = text.find(text[i : i + 3], i + 3)
            i = n if j < 0 else j + 3
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            i = j + 1
        elif c == "@":
            j = text.find("@", i + 1)
            i = n if j < 0 else j + 1
        elif c in "([":
            paren += 1
            i += 1
        elif c in ")]":
            paren = max(0, paren - 1)
            i += 1
        elif c == "{":
            depth += 1
            if paren == 0:
                braces.append((i, c, depth))
            i += 1
        elif c == "}":
            if paren == 0:
                braces.append((i, c, depth))
            depth = max(0, depth - 1)
            i += 1
        else:
            i += 1
    return headers, braces


def usda_prim_line(text: str, prim_path: str) -> int | None:
    """Best-effort 1-based line of the spec that introduces ``prim_path`` in usda text.

    Follows the prim hierarchy: each path component must be a direct child (one
    brace level deeper, inside the parent's body) of the previous one. Property
    paths use their prim. Returns None whenever the hierarchy cannot be
    established, for example when the prim is defined in another layer.
    """
    names = [n for n in prim_path.split(".", 1)[0].split("/") if n]
    if not names:
        return None
    headers, braces = _scan_usda(text)
    lo, hi, depth = 0, len(text), 0
    found = None
    for index, name in enumerate(names):
        found = next(
            (h for h in headers if lo <= h[0] < hi and h[2] == depth and h[1] == name), None
        )
        if found is None:
            return None
        if index == len(names) - 1:
            break
        # The prim's body: the first "{" opening depth + 1 after the header, up to
        # the "}" that closes that same depth.
        body = next(
            (b for b in braces if b[0] > found[0] and b[1] == "{" and b[2] == depth + 1), None
        )
        if body is None or body[0] >= hi:
            return None
        close = next(
            (b for b in braces if b[0] > body[0] and b[1] == "}" and b[2] == depth + 1), None
        )
        lo, hi, depth = body[0] + 1, (close[0] if close else len(text)), depth + 1
    return text.count("\n", 0, found[0]) + 1


def read_usda_text(abs_path: str) -> str | None:
    """The file's text if it is a usda text layer (by header), else None."""
    try:
        with open(abs_path, "rb") as fh:
            head = fh.read(8)
            if not head.startswith(b"#usda"):
                return None
            return (head + fh.read()).decode("utf-8", errors="replace")
    except OSError:
        return None
