#!/usr/bin/env python3
"""
Якорь задачи для передачи между сессиями (скилл handoff).

TASK.md хранит слова владельца дословно и только дополняется. Промпт новой
сессии собирается отсюда механически, без пересказа: так цель не дрейфует
от перехода к переходу.

    handoff_anchor.py init   <dir> [--file F] [--at T]  # первое сообщение владельца (stdin или файл)
    handoff_anchor.py append <dir> [--file F] [--at T] [--kind goal|procedure]
    handoff_anchor.py check  <dir>                     # прежние записи не тронуты
    handoff_anchor.py prompt <dir> [--state STATE.md]  # промпт для spawn_task в stdout

goal — сообщение, которое задаёт или меняет цель, объём, приёмку.
procedure — «запускай», «через krasinsky-task», «сделай прогон»: в якорь цели не входит.
"""
import argparse
import datetime
import hashlib
import fcntl
import json
import os
import re
import sys
from pathlib import Path
import tempfile

TASK = "TASK.md"
SEAL = ".TASK.seal.json"
MARKER = "ЯКОРЬ ЗАДАЧИ"
HEADER = (
    "# TASK: слова владельца дословно\n\n"
    "Файл только дополняется (`handoff_anchor.py append`). Не редактировать, не пересказывать.\n"
    "Цель работы — то, что сказано здесь, а не пересказ в STATE.md.\n"
)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def read_text(args):
    t = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
    t = t.strip("\n")
    if not t.strip():
        sys.exit("пустой текст: нечего записывать")
    return t


def write_atomic(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staged:
        staged.write(data)
        staged.flush()
        os.fsync(staged.fileno())
    os.replace(staged.name, path)


def recover(directory):
    pending = directory / ".TASK.pending.json"
    if not pending.exists():
        return
    tx = json.loads(pending.read_text(encoding="utf-8"))
    target = directory / TASK
    current = target.read_bytes() if target.exists() else b""
    proposed = tx["text"].encode("utf-8")
    if sha(current) not in (tx["previous"], sha(proposed)):
        sys.exit("Содержимое TASK.md изменилось во время незавершённой записи.")
    if current != proposed:
        write_atomic(target, proposed)
    write_atomic(directory / SEAL, json.dumps(tx["seals"], ensure_ascii=False).encode("utf-8"))
    pending.unlink()


def commit(directory, previous, added):
    directory = Path(directory)
    raw = previous + added.encode("utf-8")
    seals = json.loads((directory / SEAL).read_text(encoding="utf-8")) if previous else []
    seals.append({"len": len(raw), "sha256": sha(raw), "at": now()})
    tx = {"previous": sha(previous), "text": raw.decode("utf-8"), "seals": seals}
    write_atomic(directory / ".TASK.pending.json", json.dumps(tx, ensure_ascii=False).encode("utf-8"))
    recover(directory)


def valid_at(v):
    try:
        datetime.datetime.strptime(v, "%Y-%m-%d %H:%M")
    except ValueError:
        raise argparse.ArgumentTypeError("нужен формат YYYY-MM-DD HH:MM")
    return v


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def entry(n, kind, text, at=None):
    tag = "цель" if kind == "goal" else "процедура"
    return f"\n## {n}. {at or now()} — {tag}\n\n{text}\n"


def cmd_init(a):
    p = os.path.join(a.dir, TASK)
    if os.path.exists(p):
        sys.exit(f"{p} уже есть: дополняй через append, не пересоздавай")
    if Path(a.dir, SEAL).exists():
        sys.exit("В каталоге уже есть журнал контрольных сумм. Проверьте исходную передачу.")
    commit(a.dir, b"", HEADER + entry(1, "goal", a.text, a.at))
    print(p)


def cmd_append(a):
    previous, records = check(a.dir)
    commit(a.dir, previous, entry(len(records) + 1, a.kind, a.text, a.at))
    print(os.path.join(a.dir, TASK))


def check(d):
    p, sp = os.path.join(d, TASK), os.path.join(d, SEAL)
    if not os.path.exists(p):
        sys.exit(f"нет {p}: сначала init со словами владельца")
    if not os.path.exists(sp):
        sys.exit(f"нет печати {sp}: TASK.md создан мимо скрипта; пересоздай через init")
    b = open(p, "rb").read()
    log = json.loads(Path(sp).read_text(encoding="utf-8"))
    if not log or not b.startswith(HEADER.encode("utf-8")):
        sys.exit("Неверный заголовок или пустой журнал контрольных сумм.")
    start = len(HEADER.encode("utf-8"))
    records = []
    for s in log:
        if not start < s["len"] <= len(b):
            sys.exit("Нарушены границы сохранённых сообщений.")
        if len(b) < s["len"] or sha(b[: s["len"]]) != s["sha256"]:
            sys.exit(f"TASK.md изменён задним числом (печать от {s['at']}): верни прежний текст, новое — только append")
        records.append(b[start:s["len"]].decode("utf-8").lstrip("\n"))
        start = s["len"]
    if len(b) != log[-1]["len"]:
        sys.exit("TASK.md дописан мимо append: печать не совпадает с концом файла")
    return b, records


def section(md, name):
    """Последний раздел, чей заголовок начинается с name: в живом STATE свежий «Дальше» ниже."""
    ms = list(re.finditer(rf"^## {re.escape(name)}[^\n]*\n(.*?)(?=^## |\Z)", md, re.M | re.S))
    return ms[-1].group(1).strip() if ms else ""


def cmd_prompt(a):
    b, records = check(a.dir)
    # Sealed byte boundaries keep Markdown headings inside verbatim messages.
    body = "\n".join(r for r in records if r.splitlines()[0].endswith("— цель"))
    proc = "\n".join(r for r in records if r.splitlines()[0].endswith("— процедура"))
    state = a.state or os.path.join(a.dir, "STATE.md")
    smd = open(state, encoding="utf-8").read() if os.path.exists(state) else ""
    out = [
        f"Продолжение работы. Каталог задачи: {os.path.abspath(a.dir)}",
        "",
        f"{MARKER} (TASK.md, sha256 {sha(b)[:12]}). Это слова владельца дословно. "
        "Это и есть цель; STATE.md — только журнал состояния.",
        "",
        body.strip(),
    ]
    if proc.strip():
        out += ["", "Процедурные указания владельца (как работать, не что сделать):", "", proc.strip()]
    for name in ("Приёмка", "Дальше", "Не повторять"):
        s = section(smd, name)
        if s:
            out += ["", f"Из STATE.md, раздел «{name}» (без правок):", s]
    out += [
        "",
        "Порядок:",
        f"1. Прочитай {os.path.join(os.path.abspath(a.dir), TASK)} и {os.path.abspath(state)}.",
        "2. Если текущий шаг из «Дальше» не ведёт к цели из TASK.md, сначала скажи об этом владельцу.",
        "3. Перед сдачей сверь результат с каждым сообщением-целью из TASK.md по пунктам: "
        "выполнено / не выполнено / изменено владельцем (где). Сверку покажи владельцу.",
        "4. Новые слова владельца о цели, объёме или приёмке добавляй: handoff_anchor.py append. Свой пересказ в TASK.md не пиши.",
    ]
    print("\n".join(out))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("init", "append"):
        p = sp.add_parser(n)
        p.add_argument("dir")
        p.add_argument("--file")
        p.add_argument("--at", type=valid_at, help="когда владелец это сказал, YYYY-MM-DD HH:MM")
        if n == "append":
            p.add_argument("--kind", choices=("goal", "procedure"), default="goal")
    p = sp.add_parser("check"); p.add_argument("dir")
    p = sp.add_parser("prompt"); p.add_argument("dir"); p.add_argument("--state")
    a = ap.parse_args()
    if a.cmd in ("init", "append"):
        a.text = read_text(a)
    directory = Path(a.dir)
    if a.cmd == "init":
        directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        sys.exit("Каталог передачи не найден.")
    with (directory / ".TASK.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        recover(directory)
        if a.cmd == "check":
            check(a.dir)
            print("TASK.md цел")
        else:
            {"init": cmd_init, "append": cmd_append, "prompt": cmd_prompt}[a.cmd](a)


if __name__ == "__main__":
    main()
