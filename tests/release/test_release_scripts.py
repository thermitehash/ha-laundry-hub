"""Tests for script/check_versions.py and script/check_version_bump.py."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "script" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


versions = _load("check_versions")
bump = _load("check_version_bump")


# --- check_versions -----------------------------------------------------------


def make_tree(root: Path, version: str = "1.2.3", **overrides: str | None) -> Path:
    """Write a minimal repo with every version copy set to `version`."""
    values = {
        "manifest": version,
        "const": version,
        "pyproject": version,
        "firmware": version,
        "notes": f"# v{version}: Title\n\n- Bullet\n",
    }
    values.update(overrides)
    comp = root / "custom_components" / "laundry_hub"
    comp.mkdir(parents=True)
    (comp / "manifest.json").write_text(
        json.dumps({"domain": "laundry_hub", "version": values["manifest"]})
    )
    if values["const"] is not None:
        (comp / "const.py").write_text(f'VERSION: Final = "{values["const"]}"\n')
    if values["pyproject"] is not None:
        (root / "pyproject.toml").write_text(
            f'[project]\nname = "x"\nversion = "{values["pyproject"]}"\n'
        )
    if values["firmware"] is not None:
        fw = root / "esphome" / "tap-station" / "packages"
        fw.mkdir(parents=True)
        (fw / "tap-station.yaml").write_text(
            f'substitutions:\n  firmware_version: "{values["firmware"]}"\n'
        )
    if values["notes"] is not None:
        rel = root / "docs" / "releases"
        rel.mkdir(parents=True)
        (rel / f"v{values['manifest']}.md").write_text(values["notes"])
    return root


def test_repo_itself_is_consistent() -> None:
    assert versions.check(ROOT) == []
    assert versions.main(["x", str(ROOT)]) == 0


def test_consistent_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_tree(tmp_path)
    assert versions.check(tmp_path) == []
    assert versions.main(["x", str(tmp_path)]) == 0
    assert "1.2.3 is consistent" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"const": "1.2.2"}, "const.py has '1.2.2'"),
        ({"pyproject": "1.3.0"}, "pyproject.toml has '1.3.0'"),
        ({"firmware": "0.1.0"}, "tap-station.yaml has '0.1.0'"),
        ({"const": None}, "const.py: version not found"),
        ({"pyproject": None}, "pyproject.toml: version not found"),
        ({"firmware": None}, "tap-station.yaml: version not found"),
        ({"notes": None}, "missing release notes: docs/releases/v1.2.3.md"),
        ({"notes": "# Release 1.2.3\n"}, "must start with '# v1.2.3: <title>'"),
        ({"notes": ""}, "must start with '# v1.2.3: <title>'"),
    ],
)
def test_inconsistencies_reported(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    override: dict[str, str | None],
    fragment: str,
) -> None:
    make_tree(tmp_path, **override)
    errors = versions.check(tmp_path)
    assert any(fragment in e for e in errors), errors
    assert versions.main(["x", str(tmp_path)]) == 1
    assert "ERROR:" in capsys.readouterr().out


def test_non_semver_manifest(tmp_path: Path) -> None:
    make_tree(tmp_path, version="1.2")
    assert any("is not X.Y.Z" in e for e in versions.check(tmp_path))


# --- check_version_bump --------------------------------------------------------


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo with main at manifest 1.2.3, checked out on a PR branch."""
    if shutil.which("git") is None:  # pragma: no cover
        pytest.skip("git not installed")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "commit.gpgsign", "false")
    git(tmp_path, "config", "tag.gpgsign", "false")
    write_manifest(tmp_path, "1.2.3")
    (tmp_path / "README.md").write_text("hi\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "checkout", "-qb", "pr")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_manifest(repo: Path, version: str) -> None:
    comp = repo / "custom_components" / "laundry_hub"
    comp.mkdir(parents=True, exist_ok=True)
    (comp / "manifest.json").write_text(json.dumps({"version": version}))


def commit(repo: Path, rel: str, text: str = "x\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"change {rel}")


@pytest.mark.parametrize(
    "rel", ["README.md", "docs/wled.md", ".github/workflows/ci.yml", "tests/test_x.py"]
)
def test_docs_and_ci_only_exempt(
    repo: Path, capsys: pytest.CaptureFixture[str], rel: str
) -> None:
    commit(repo, rel)
    assert bump.main(["x", "main"]) == 0
    assert "no version bump needed" in capsys.readouterr().out


@pytest.mark.parametrize(
    "rel",
    ["custom_components/laundry_hub/hub.py", "esphome/tap-station/packages/x.yaml"],
)
def test_shipped_change_without_bump_fails(
    repo: Path, capsys: pytest.CaptureFixture[str], rel: str
) -> None:
    commit(repo, rel)
    assert bump.main(["x", "main"]) == 1
    out = capsys.readouterr().out
    assert "not above 1.2.3 on the base branch (main)" in out
    assert "Bump 'version' in custom_components/laundry_hub/manifest.json" in out


def test_shipped_change_with_bump_passes(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, "custom_components/laundry_hub/hub.py")
    write_manifest(repo, "1.2.4")
    git(repo, "commit", "-qam", "bump")
    assert bump.main(["x", "main"]) == 0
    assert "1.2.4 is above the base branch" in capsys.readouterr().out


def test_compares_as_versions_not_strings(repo: Path) -> None:
    # "1.10.0" < "1.9.0" as strings but greater as versions.
    write_manifest(repo, "1.9.0")
    git(repo, "commit", "-qam", "1.9.0")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "pr")
    git(repo, "checkout", "-q", "pr")
    commit(repo, "custom_components/laundry_hub/hub.py")
    write_manifest(repo, "1.10.0")
    git(repo, "commit", "-qam", "bump")
    assert bump.main(["x", "main"]) == 0


def test_downgrade_fails(repo: Path) -> None:
    commit(repo, "custom_components/laundry_hub/hub.py")
    write_manifest(repo, "1.2.2")
    git(repo, "commit", "-qam", "down")
    assert bump.main(["x", "main"]) == 1


def test_must_also_beat_latest_tag(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(repo, "tag", "v1.3.0", "main")
    commit(repo, "custom_components/laundry_hub/hub.py")
    write_manifest(repo, "1.2.4")
    git(repo, "commit", "-qam", "bump")
    assert bump.main(["x", "main"]) == 1
    assert "the latest tag v1.3.0" in capsys.readouterr().out
    write_manifest(repo, "1.3.1")
    git(repo, "commit", "-qam", "bump again")
    assert bump.main(["x", "main"]) == 0


def test_first_release_no_base_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "commit.gpgsign", "false")
    commit(tmp_path, "README.md")
    git(tmp_path, "checkout", "-qb", "pr")
    write_manifest(tmp_path, "0.1.0")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "add integration")
    monkeypatch.chdir(tmp_path)
    assert bump.main(["x", "main"]) == 0
    assert "will be the first release" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("raw", "parsed"), [("1.2.3", (1, 2, 3)), ("v10.0.1", (10, 0, 1))]
)
def test_parse(raw: str, parsed: tuple[int, int, int]) -> None:
    assert bump._parse(raw) == parsed


@pytest.mark.parametrize("raw", ["1.2", "1.2.3-beta", "x"])
def test_parse_rejects(raw: str) -> None:
    with pytest.raises(SystemExit):
        bump._parse(raw)


def test_needs_bump() -> None:
    assert not bump.needs_bump(["esphome/README.md"])
    assert bump.needs_bump(["esphome/tap-station/components/st25r_i2c/st25r_i2c.cpp"])
    assert bump.needs_bump(["custom_components/laundry_hub/strings.json"])
    assert not bump.needs_bump(["docs/releases/v0.1.2.md", "README.md", "hacs.json"])
