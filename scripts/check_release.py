from __future__ import annotations

import argparse
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _match(pattern: str, text: str, *, source: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise ValueError(f"Could not find release version in {source}")
    return match.group(1)


def validate_release(tag: str) -> list[str]:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    module = (PROJECT_ROOT / "src" / "escape_lab" / "__init__.py").read_text(
        encoding="utf-8"
    )
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    project_version = _match(
        r'^version = "([^"]+)"$',
        pyproject,
        source="pyproject.toml",
    )
    module_version = _match(
        r'^__version__ = "([^"]+)"$',
        module,
        source="src/escape_lab/__init__.py",
    )

    errors: list[str] = []
    expected_tag = f"v{project_version}"
    if tag != expected_tag:
        errors.append(f"tag {tag!r} does not match {expected_tag!r}")
    if module_version != project_version:
        errors.append(
            f"module version {module_version!r} does not match "
            f"project version {project_version!r}"
        )
    if f"## {project_version} - " not in changelog:
        errors.append(f"CHANGELOG.md has no dated {project_version} section")
    if f"Public beta — {project_version}" not in readme:
        errors.append(f"README.md has no public-beta {project_version} notice")
    if "Development Status :: 4 - Beta" not in pyproject:
        errors.append("pyproject.toml is not classified as Beta")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate release tag and repository version metadata."
    )
    parser.add_argument("--tag", required=True)
    arguments = parser.parse_args()

    errors = validate_release(arguments.tag)
    if errors:
        for error in errors:
            print(f"release check failed: {error}")
        return 1

    print(f"Release metadata valid for {arguments.tag}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
