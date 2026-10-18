"""House rules: checks with no upstream OpenUSD equivalent.

Each module exposes ``check(ctx, settings) -> list[Finding]`` (or, for checks that
run before the stage is opened, ``check_before_open``). ``settings`` is the
effective per-file rule configuration, including ``severity``.
"""
