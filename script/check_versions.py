"""Fail if the version copies disagree with manifest.json or release notes are missing.

Source of truth: custom_components/laundry_hub/manifest.json -> "version".
Must match: const.py VERSION, pyproject.toml [project].version and the
ESPHome package's firmware_version substitution. docs/releases/v<version>.md
must exist and start with "# v<version>: ".

Usage: python script/check_versions.py [repo-root]
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import tomllib

MANIFEST = "custom_components/laundry_hub/manifest.json"
CONST = "custom_components/laundry_hub/const.py"
PYPROJECT = "pyproject.toml"
FIRMWARE = "esphome/tap-station/packages/tap-station.yaml"
SEMVER = re.compile(r"\d+\.\d+\.\d+")


def manifest_version(root: pathlib.Path) -> str:
    """Return the manifest version (the source of truth)."""
    return str(json.loads((root / MANIFEST).read_text())["version"])


def _regex(root: pathlib.Path, rel: str, pattern: str) -> str | None:
    path = root / rel
    if not path.is_file():
        return None
    match = re.search(pattern, path.read_text(), re.MULTILINE)
    return match.group(1) if match else None


def copies(root: pathlib.Path) -> dict[str, str | None]:
    """Every place that repeats the version, keyed by file."""
    pyproject = root / PYPROJECT
    py_version = (
        tomllib.loads(pyproject.read_text()).get("project", {}).get("version")
        if pyproject.is_file()
        else None
    )
    return {
        CONST: _regex(root, CONST, r'^VERSION:\s*Final\s*=\s*"([^"]+)"'),
        PYPROJECT: py_version,
        FIRMWARE: _regex(root, FIRMWARE, r'^\s*firmware_version:\s*"?([^"\s#]+)"?'),
    }


def check(root: pathlib.Path) -> list[str]:
    """Return a list of problems (empty when consistent)."""
    version = manifest_version(root)
    errors: list[str] = []
    if not SEMVER.fullmatch(version):
        errors.append(f"{MANIFEST} version {version!r} is not X.Y.Z")
    for rel, found in copies(root).items():
        if found is None:
            errors.append(f"{rel}: version not found")
        elif found != version:
            errors.append(
                f"{rel} has {found!r} but {MANIFEST} has {version!r}; "
                "set them to the same version"
            )
    notes = root / "docs" / "releases" / f"v{version}.md"
    if not notes.is_file():
        errors.append(f"missing release notes: docs/releases/v{version}.md")
    else:
        first = notes.read_text().splitlines()[:1]
        if not first or not first[0].startswith(f"# v{version}: "):
            errors.append(
                f"docs/releases/v{version}.md must start with '# v{version}: <title>'"
            )
    return errors


def main(argv: list[str]) -> int:
    """CLI entry point."""
    root = pathlib.Path(argv[1]) if len(argv) > 1 else pathlib.Path()
    errors = check(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Version {manifest_version(root)} is consistent; release notes present.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
