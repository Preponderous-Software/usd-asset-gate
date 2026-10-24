"""Advisory before/after visual diffs of changed USD assets, rendered headless by Strata.

Design: RFC 0028 (Preponderous-Software/rfcs). This package is imported only by the
``preview`` subcommand and by ``comment --previews``; ``check`` never imports it, so the
core gate keeps running on ``usd-core`` and ``PyYAML`` alone. Its extra dependency is
numpy (``pip install 'usd-asset-gate[preview]'``) plus a Strata ``libstrata.so``.
"""
