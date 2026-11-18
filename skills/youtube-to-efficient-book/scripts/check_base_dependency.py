#!/usr/bin/env python3
"""Fail closed when the shared youtube-to-book process has changed."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-root", type=Path, help="Override base root for an isolated test")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parents[1] / "references/base-dependency.json")
    args = parser.parse_args()
    expected = json.loads(args.manifest.read_text(encoding="utf-8"))
    base = Path(expected["base_root"]).expanduser()
    if not base.is_absolute():
        base = (Path(__file__).resolve().parents[1] / base).resolve()  # соседняя папка youtube-to-book
    root = args.base_root or base
    changed = []
    for relative, digest in sorted(expected["files"].items()):
        path = root / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual != digest:
            changed.append(relative)
    if changed:
        print("BASE_DEPENDENCY_CHANGED: " + ", ".join(changed))
        print("Stop the book pipeline; inspect changes and issue a reviewed skill revision before continuing.")
        return 1
    print(f"BASE_DEPENDENCY_OK: {len(expected['files'])} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
