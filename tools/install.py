#!/usr/bin/env python3
"""Install self-contained skill folders without replacing a local edition."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def contents(folder):
    """Compare installed files by content, allowing generated Python caches."""
    if folder.is_symlink() or not folder.is_dir():
        return None
    files = {}
    for path in folder.rglob("*"):
        rel = path.relative_to(folder)
        if "__pycache__" in rel.parts:
            continue
        if path.is_symlink():
            return None
        if path.is_file():
            files[rel] = hashlib.sha256(path.read_bytes()).digest()
    return files


def closure(names, catalog):
    result = set()
    queue = list(names)
    while queue:
        name = queue.pop()
        if name in result:
            continue
        if name not in catalog:
            raise ValueError(f"Неизвестный скилл: {name}")
        result.add(name)
        queue.extend(catalog[name]["requires"])
    return sorted(result)


def install(target, names, dry_run):
    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    chosen = closure(names or list(catalog), catalog)
    pending = []
    for name in chosen:
        src = ROOT / "skills" / name
        if not (src / "SKILL.md").is_file():
            raise ValueError(f"В комплекте отсутствует {name}/SKILL.md")
        dst = target / name
        if os.path.lexists(dst):
            if contents(dst) != contents(src) or dst.is_symlink():
                raise ValueError(f"В {dst} уже есть другая версия. Выберите отдельный каталог или перенесите её самостоятельно.")
        else:
            pending.append(name)

    print("Выбрано: " + ", ".join(chosen))
    if not pending:
        print("Выбранные версии уже установлены.")
        return
    if dry_run:
        print(f"Будут установлены в {target}: " + ", ".join(pending))
        return
    target.mkdir(parents=True, exist_ok=True)
    # Stage complete directories on the target filesystem before moving any of them.
    with tempfile.TemporaryDirectory(prefix=".skill-install-", dir=target) as tmp:
        stage = Path(tmp)
        for name in pending:
            shutil.copytree(ROOT / "skills" / name, stage / name)
        for name in pending:
            dst = target / name
            # A concurrent run may already have completed this exact installation.
            if os.path.lexists(dst):
                if not dst.is_symlink() and contents(dst) == contents(stage / name):
                    continue
                raise ValueError(f"Каталог изменился во время установки: {dst}")
            (stage / name).rename(dst)
    print(f"Установка завершена: {target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True, help="Каталог skills выбранного агента")
    parser.add_argument("--dry-run", action="store_true", help="Показать план установки")
    parser.add_argument("skills", nargs="*", help="Имена скиллов; по умолчанию весь комплект")
    args = parser.parse_args()
    try:
        install(args.target.expanduser().absolute(), args.skills, args.dry_run)
    except (OSError, ValueError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
