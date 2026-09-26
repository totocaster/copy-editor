"""Cut a release, or print a release's notes.

    make release VERSION=0.3.0              # or: python3 scripts/release.py 0.3.0
    python3 scripts/release.py notes 0.3.0

A release starts from a clean `main` whose CHANGELOG.md has notes under
"## Unreleased". The script runs the tests, writes the version into
pyproject.toml, package.json, package-lock.json and uv.lock, dates the
changelog section, commits, and creates an annotated tag. It never pushes:
pushing the tag runs .github/workflows/release.yml, which publishes the
GitHub Release from the same changelog section.

Standard library only, so CI can run `notes` without installing anything.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
PYPROJECT = ROOT / "pyproject.toml"
VERSIONED_FILES = ["pyproject.toml", "package.json", "package-lock.json", "uv.lock", "CHANGELOG.md"]
UNRELEASED = "## Unreleased"
SEMVER = re.compile(r"\d+\.\d+\.\d+")
PROJECT_VERSION = re.compile(r'^version = "([^"]*)"$', re.MULTILINE)


class ReleaseError(Exception):
    pass


def section(changelog: str, heading: str) -> str:
    """Body under a `## ` heading (matched by prefix), up to the next `## ` heading."""
    lines = changelog.splitlines()
    start = next((i for i, line in enumerate(lines) if line == heading or line.startswith(heading + " ")), None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1:end]).strip()


def release_notes(changelog: str, version: str) -> str:
    notes = section(changelog, f"## {version}")
    if not notes:
        raise ReleaseError(f"CHANGELOG.md has no notes for {version}")
    return notes


BLOCK_START = re.compile(r"(#|[-*+] |\d+[.)] |>|\||```)")


def unwrap(markdown: str) -> str:
    """Join hard-wrapped lines: GitHub renders every newline in release notes as a line break."""
    out: list[str] = []
    fenced = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
        elif not fenced and stripped and out and out[-1].strip() and not out[-1].lstrip().startswith(("#", "|", "```")) \
                and not BLOCK_START.match(stripped):
            out[-1] = f"{out[-1].rstrip()} {stripped}"
            continue
        out.append(line)
    return "\n".join(out)


def date_unreleased(changelog: str, version: str, day: date) -> str:
    """Turn the Unreleased notes into the `version` section and open a fresh Unreleased one."""
    if not section(changelog, UNRELEASED):
        raise ReleaseError(f'CHANGELOG.md needs notes under "{UNRELEASED}" before a release')
    if section(changelog, f"## {version}"):
        raise ReleaseError(f"CHANGELOG.md already has a {version} section")
    return changelog.replace(f"{UNRELEASED}\n", f"{UNRELEASED}\n\n## {version} - {day.isoformat()}\n", 1)


def current_version() -> str:
    match = PROJECT_VERSION.search(PYPROJECT.read_text())
    if match is None:
        raise ReleaseError("pyproject.toml has no version")
    return match[1]


def parse(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def run(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def cut(version: str) -> None:
    if not SEMVER.fullmatch(version):
        raise ReleaseError(f"{version!r} is not a version like 1.2.3")
    tag = f"v{version}"
    if run("git", "rev-parse", "--abbrev-ref", "HEAD") != "main":
        raise ReleaseError("releases are cut from main")
    if run("git", "status", "--porcelain"):
        raise ReleaseError("commit or stash your changes first")
    if run("git", "tag", "--list", tag):
        raise ReleaseError(f"{tag} already exists")
    previous = current_version()
    if parse(version) <= parse(previous):
        raise ReleaseError(f"{version} is not newer than {previous}")
    changelog = date_unreleased(CHANGELOG.read_text(), version, date.today())

    print("Running the tests…", flush=True)
    subprocess.run(["make", "test"], cwd=ROOT, check=True)

    try:
        PYPROJECT.write_text(PROJECT_VERSION.sub(f'version = "{version}"', PYPROJECT.read_text(), count=1))
        CHANGELOG.write_text(changelog)
        run("npm", "version", version, "--no-git-tag-version", "--allow-same-version")
        run("uv", "lock", "--quiet")
        run("git", "add", *VERSIONED_FILES)
        run("git", "commit", "--quiet", "-m", f"release: {tag}")
    except BaseException:
        subprocess.run(["git", "checkout", "HEAD", "--", *VERSIONED_FILES], cwd=ROOT)
        raise
    subprocess.run(["git", "tag", "--annotate", tag, "--file", "-"], cwd=ROOT, check=True,
                   input=f"Copy Editor {version}\n\n{release_notes(changelog, version)}\n", text=True)

    print(f"Tagged {tag}. Nothing is pushed yet. To publish the release:\n\n"
          f"    git push origin main {tag}\n\n"
          f"To undo instead:  git tag -d {tag} && git reset --keep HEAD~1")


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 2 and argv[0] == "notes":
            print(unwrap(release_notes(CHANGELOG.read_text(), argv[1])))
        elif len(argv) == 1 and argv[0] not in {"-h", "--help"}:
            cut(argv[0])
        else:
            print(__doc__.strip())
            return 0 if argv and argv[0] in {"-h", "--help"} else 2
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        print(f"release: {' '.join(exc.cmd)} failed" + (f"\n{detail}" if detail else ""), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
