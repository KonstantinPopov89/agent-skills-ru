#!/usr/bin/env python3
"""Offline installation smoke test for the complete collection (Python 3.10+)."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(script, *args):
    return subprocess.run([sys.executable, str(script), *map(str, args)], check=True,
                          capture_output=True, text=True).stdout


def main():
    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    sources = ROOT / "skills"
    if set(catalog) != {d.name for d in sources.iterdir() if d.is_dir()}:
        raise ValueError("Каталог и папка skills расходятся")
    if sum(entry["role"] == "featured" for entry in catalog.values()) != 15:
        raise ValueError("Ожидались 15 основных скиллов")
    with tempfile.TemporaryDirectory(prefix="skill pack ") as scratch:
        target = Path(scratch) / "test install"
        run(ROOT / "tools/install.py", "--target", target)
        run(ROOT / "tools/install.py", "--target", target)
        for name, entry in catalog.items():
            folder = target / name
            md = (folder / "SKILL.md").read_text(encoding="utf-8")
            if not md.startswith("---\n") or not re.search(rf"^name: {re.escape(name)}$", md, re.M):
                raise ValueError(f"Некорректная точка входа {name}")
            if not all((target / dep / "SKILL.md").is_file() for dep in entry["requires"]):
                raise ValueError(f"Не хватает зависимостей для {name}")
        for checker in sorted(target.glob("*/scripts/check_base_dependency.py")):
            run(checker)
        outcome = target / "outcome-task"
        run(outcome / "scripts/outcome_card_check.py", "--strict", outcome / "references/example-card.md")
        memory = target / "shared-agent-memory"
        cmd = memory / "scripts/memory.py"
        db = Path(scratch) / "memory.sqlite"
        run(cmd, "--db", db, "import", memory / "examples/conversation.jsonl")
        hits = json.loads(run(cmd, "--db", db, "search", "ОХРЫ", "--actor", "user"))
        if len(hits) != 1 or hits[0]["kind"] != "message":
            raise ValueError("Поиск не воспроизвёл учебный пример")
    print(f"Проверены установка, повторный запуск, {len(catalog)} скиллов, связи медиамодулей, карточка и поиск.")


if __name__ == "__main__":
    main()
