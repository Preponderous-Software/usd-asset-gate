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
    messages = [" ".join(m.split()) for m in _TF_ERROR_RE.findall(str(exc))]
    return messages or [" ".join(str(exc).split())]


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
