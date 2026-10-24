"""Third-party code vendored unmodified, with the release each copy came from.

``strata/`` is Strata's official Python binding (``bindings/python/strata`` of
https://github.com/Preponderous-Software/strata at the tag below, MIT, ``strata/LICENSE``).
It is vendored rather than declared as a dependency because it has no PyPI release and a
direct URL dependency would keep this package off PyPI. To update it, copy the three files
from the new tag, then update ``STRATA_BINDING`` (a test re-hashes the copies).
"""

STRATA_BINDING = {
    "tag": "v0.5.0",
    "commit": "2f997acad88d22b1249ee67ed905ba50e6a661e8",
    "sha256": {
        "__init__.py": "acc4b4eaaf5ad17d59465d4a72c83e92c623639c1380015f8c41bf7f649bbd36",
        "_abi.py": "bb06a27c3bfa9315700df6ea4cf4aa3a197ad602e9026f4dde179f52462872c5",
        "_loader.py": "38225c8a2ac2a89ce7f6691c4ce0a153a95a53cb18e6a443c1577625393becc9",
    },
}
