"""Fail if a PR changes shipped code but does not raise the manifest version.

Ported from thermitehash/aiochainway (script/check_version_bump.py), adapted
to read custom_components/laundry_hub/manifest.json. A change under
custom_components/ or esphome/ must raise the version above both the base
branch's manifest version and the latest v* tag. Docs-only and CI-only
changes are exempt, including Markdown files inside those folders.

Usage: python script/check_version_bump.py <base-ref>
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

MANIFEST = "custom_components/laundry_hub/manifest.json"
SHIPPED = ("custom_components/", "esphome/")


def _run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def _parse(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", version.strip())
    if not match:
        raise SystemExit(f"not a SemVer X.Y.Z version: {version!r}")
    major, minor, patch = (int(g) for g in match.groups())
    return major, minor, patch


def needs_bump(paths: list[str]) -> bool:
    """True if any changed path ships to users (Markdown docs never do)."""
    return any(p.startswith(SHIPPED) and not p.endswith(".md") for p in paths)


def _base_version(base: str) -> str | None:
    try:
        raw = _run("git", "show", f"{base}:{MANIFEST}")
    except subprocess.CalledProcessError:
        return None  # integration did not exist on the base branch
    return str(json.loads(raw)["version"])


def main(argv: list[str]) -> int:
    """CLI entry point."""
    base = argv[1] if len(argv) > 1 else "origin/main"
    changed = _run("git", "diff", "--name-only", f"{base}...HEAD").split()
    if not needs_bump(changed):
        print(
            "No changes under custom_components/ or esphome/; no version bump needed."
        )
        return 0
    with open(MANIFEST, encoding="utf-8") as file:
        version = str(json.load(file)["version"])
    floor: list[tuple[str, str]] = []
    if (base_version := _base_version(base)) is not None:
        floor.append((base_version, f"the base branch ({base})"))
    tags = [
        t
        for t in _run("git", "tag", "--list", "v*").split()
        if re.fullmatch(r"v\d+\.\d+\.\d+", t)
    ]
    if tags:
        latest = max(tags, key=_parse)
        floor.append((latest.removeprefix("v"), f"the latest tag {latest}"))
    for other, where in floor:
        if _parse(version) <= _parse(other):
            print(
                f"ERROR: this PR changes custom_components/ or esphome/ but the "
                f"manifest version {version} is not above {other} on {where}.\n"
                f"Bump 'version' in {MANIFEST} (and const.py, pyproject.toml, "
                "firmware_version) and add docs/releases/v<new>.md."
            )
            return 1
    if not floor:
        print(f"No base version or tags; {version} will be the first release.")
        return 0
    print(f"Version {version} is above " + " and ".join(w for _, w in floor) + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
