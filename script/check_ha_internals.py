"""Check the HA internals Laundry Hub's hidden media source relies on.

Usage: python script/check_ha_internals.py PLATFORM_PY MODELS_PY
(homeassistant/helpers/integration_platform.py and media_source/models.py)

Laundry Hub registers its media source directly in media_source's lazy
platform cache (``LazyIntegrationPlatforms._processed``), so it resolves for AI
Task without being listed in the media browser. This fails loudly if a Home
Assistant version renames or removes those internals.
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys


def check(platform_src: str, models_src: str) -> list[str]:
    """Return problems (empty when the internals are as expected)."""
    problems: list[str] = []
    tree = ast.parse(platform_src)
    cls = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "LazyIntegrationPlatforms"
        ),
        None,
    )
    if cls is None:
        return ["LazyIntegrationPlatforms is gone"]
    methods = {
        n.name: n
        for n in cls.body
        if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    init = methods.get("__init__")
    if init is None or "self._processed" not in ast.unparse(init):
        problems.append(
            "LazyIntegrationPlatforms.__init__ no longer sets self._processed"
        )
    get = methods.get("async_get_platform")
    if get is None or "self._processed" not in ast.unparse(get):
        problems.append("async_get_platform no longer reads self._processed first")
    get_all = methods.get("async_get_platforms")
    if get_all is None or "platforms_exists" not in ast.unparse(get_all):
        problems.append("async_get_platforms no longer filters on platforms_exists")
    if "DATA_MEDIA_SOURCE_PLATFORMS].async_get_platform(" not in models_src:
        problems.append("media_source no longer resolves through the lazy platforms")
    return problems


def main(argv: list[str]) -> int:
    """CLI entry point."""
    platform, models = (Path(p).read_text(encoding="utf-8") for p in argv[1:3])
    problems = check(platform, models)
    for problem in problems:
        print(f"::error::{problem}")
    if not problems:
        print("Hidden media source internals: OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
