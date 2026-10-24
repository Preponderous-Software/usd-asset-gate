"""Third-party code vendored unmodified, with the release each copy came from.

``strata/`` is Strata's official Python binding (``bindings/python/strata`` of
https://github.com/Preponderous-Software/strata at the tag below, MIT, ``strata/LICENSE``).
It is vendored rather than declared as a dependency because it has no PyPI release and a
direct URL dependency would keep this package off PyPI. To update it, copy the three files
from the new tag, then update ``STRATA_BINDING`` (a test re-hashes the copies).
"""

STRATA_BINDING = {
    "tag": "v0.6.0",
    "commit": "c2d7ee4c1102726c654c90d8361383d9ba3590b3",
    "sha256": {
        "__init__.py": "86381523746aa9bf5c4a71ca734fd316972d67d1037482ed4af3bdfb8c4863cb",
        "_abi.py": "372f03171126262068962e3fa83d70a91a8353603473f59e8df0fe13a671a094",
        "_loader.py": "8b2e47f33ae815f6a73d6a1a944d70b17feceeaa9b544d4711646e0b302283db",
    },
}
