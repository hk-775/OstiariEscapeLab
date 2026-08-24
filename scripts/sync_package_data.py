from __future__ import annotations

import argparse
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DATA = PROJECT_ROOT / "src" / "escape_lab" / "data"
COPIES = {
    PROJECT_ROOT / "baselines" / "first-product.json": (
        PACKAGE_DATA / "baselines" / "first-product.json"
    ),
    PROJECT_ROOT / "baselines" / "gvisor-fixture.json": (
        PACKAGE_DATA / "baselines" / "gvisor-fixture.json"
    ),
    PROJECT_ROOT / "scenarios" / "catalog.json": (
        PACKAGE_DATA / "scenarios" / "catalog.json"
    ),
    PROJECT_ROOT / "schemas" / "scenario-manifest.schema.json": (
        PACKAGE_DATA / "schemas" / "scenario-manifest.schema.json"
    ),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize reviewed source data into the Python package"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail instead of updating when a packaged copy is stale",
    )
    args = parser.parse_args(argv)

    stale: list[Path] = []
    for source, destination in COPIES.items():
        if destination.is_file() and destination.read_bytes() == source.read_bytes():
            continue
        stale.append(destination)
        if not args.check:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

    if stale:
        for path in stale:
            state = "stale" if args.check else "updated"
            print(f"{state}: {path.relative_to(PROJECT_ROOT)}")
        return 1 if args.check else 0

    print("Packaged data matches the reviewed source files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
