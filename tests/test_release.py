import json
import tomllib
from datetime import date
from pathlib import Path

import pytest

import app
from scripts.release import ReleaseError, date_unreleased, main, release_notes, section, unwrap

ROOT = Path(__file__).resolve().parent.parent

CHANGELOG = """# Changelog

Intro.

## Unreleased

### Added

- Archive documents.

## 0.2.0 - 2026-09-26

### Fixed

- The launcher prints the full address.
"""


def test_every_manifest_carries_the_same_version():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    package = json.loads((ROOT / "package.json").read_text())
    lock = json.loads((ROOT / "package-lock.json").read_text())
    uv_lock = tomllib.loads((ROOT / "uv.lock").read_text())
    uv_version = next(p["version"] for p in uv_lock["package"] if p["name"] == "copy-editor")
    assert app.__version__ == version
    assert package["version"] == lock["version"] == lock["packages"][""]["version"] == version
    assert uv_version == version


def test_release_notes_are_the_section_body():
    assert release_notes(CHANGELOG, "0.2.0") == "### Fixed\n\n- The launcher prints the full address."
    assert section(CHANGELOG, "## Unreleased") == "### Added\n\n- Archive documents."
    with pytest.raises(ReleaseError):
        release_notes(CHANGELOG, "0.2")
    with pytest.raises(ReleaseError):
        release_notes(CHANGELOG, "9.9.9")


def test_dating_unreleased_opens_a_fresh_section():
    dated = date_unreleased(CHANGELOG, "0.3.0", date(2026, 10, 1))
    assert "## Unreleased\n\n## 0.3.0 - 2026-10-01\n\n### Added\n\n- Archive documents." in dated
    assert section(dated, "## Unreleased") == ""
    assert release_notes(dated, "0.3.0") == "### Added\n\n- Archive documents."
    assert release_notes(dated, "0.2.0") == release_notes(CHANGELOG, "0.2.0")


def test_dating_refuses_empty_or_duplicate_notes():
    with pytest.raises(ReleaseError, match="needs notes"):
        date_unreleased(date_unreleased(CHANGELOG, "0.3.0", date(2026, 10, 1)), "0.4.0", date(2026, 10, 2))
    with pytest.raises(ReleaseError, match="already has"):
        date_unreleased(CHANGELOG, "0.2.0", date(2026, 10, 1))


def test_unwrap_joins_wrapped_lines_but_keeps_blocks():
    wrapped = """Intro that wraps
onto a second line.

### Added

- A list item that wraps
  onto an indented line.
- Another item.

```bash
make release
  VERSION=1.0.0
```"""
    assert unwrap(wrapped) == """Intro that wraps onto a second line.

### Added

- A list item that wraps onto an indented line.
- Another item.

```bash
make release
  VERSION=1.0.0
```"""


def test_cli_rejects_bad_versions_before_touching_anything(capsys):
    assert main(["v1.0"]) == 1
    assert "not a version" in capsys.readouterr().err
    assert main([]) == 2
