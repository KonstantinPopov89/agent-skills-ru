#!/usr/bin/env python3
"""Local, opt-in conversation index. Python 3.9+, no server or packages."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

APP_ID = 0x53414D31  # SAM1
SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
 id TEXT PRIMARY KEY, source TEXT NOT NULL, session TEXT NOT NULL,
 record INTEGER NOT NULL, selector TEXT NOT NULL, actor TEXT NOT NULL,
 kind TEXT NOT NULL, timestamp TEXT, body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS location ON items (source, record);
"""
UNTRUSTED_MARKERS = (
    "<system-reminder", "<command-", "<local-command-", "<task-notification",
    "<environment_context", "<permissions ", "<instructions>", "# agents.md",
    "<user_instructions", "<turn_aborted", "<subagent_notification", "<skill>",
    "<user_shell_command", "<tool_response", "[request interrupted",
)


def extract(row):
    """Index visible conversational text only; never tool results or reasoning."""
    if row.get("isMeta") or row.get("isSidechain") or row.get("isCompactSummary"):
        return
    stamp = row.get("timestamp")
    if row.get("type") == "response_item":
        payload = row.get("payload", {})
        if payload.get("type") != "message":
            return
        actor = payload.get("role")
        content = payload.get("content")
        prefix = "payload.content"
        kinds = {"user": ("input_text",), "assistant": ("output_text",)}
    elif row.get("type") in ("assistant", "user"):
        payload = row.get("message", {})
        actor = payload.get("role")
        if actor != row.get("type"):
            return
        content = payload.get("content")
        prefix = "message.content"
        kinds = {"user": ("text",), "assistant": ("text",)}
    else:
        return
    if actor not in kinds:
        return
    if isinstance(content, str):
        parts = [(prefix, content)]
    elif isinstance(content, list):
        # Mixed tool-result envelopes can contain synthetic explanatory text.
        if actor == "user" and any(not isinstance(p, dict) or p.get("type") not in kinds[actor] for p in content):
            return
        parts = [(f"{prefix}[{i}].text", p.get("text"))
                 for i, p in enumerate(content) if isinstance(p, dict) and p.get("type") in kinds[actor]]
    else:
        return
    for selector, body in parts:
        if not isinstance(body, str) or not body.strip():
            continue
        if actor == "user" and any(marker in body.lower() for marker in UNTRUSTED_MARKERS):
            continue
        yield actor, selector, body, stamp


def import_file(con, path):
    if not path.is_file():
        raise ValueError(f"Файл не найден: {path}")
    source = str(path.resolve())
    session = path.stem
    count = 0
    with con, path.open(encoding="utf-8") as f:
        con.execute("DELETE FROM items WHERE source = ? AND kind = 'message'", (source,))
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("ожидалась запись JSON")
                if row.get("type") == "session_meta":
                    session = row.get("payload", {}).get("id") or session
                for actor, selector, body, stamp in extract(row):
                    record_session = row.get("sessionId") or session
                    key = hashlib.sha256(json.dumps([source, n, selector]).encode()).hexdigest()
                    con.execute("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?)",
                                (key, source, record_session, n, selector, actor, "message", stamp, body))
                    count += 1
            except (ValueError, AttributeError, TypeError) as exc:
                raise ValueError(f"{path}, строка {n}: {exc}") from exc
    return {"indexed_blocks": count, "source": source}


def search(con, args):
    words = args.query.split()
    if not words:
        raise ValueError("Введите непустой запрос")
    # Python casefold handles Cyrillic on SQLite builds without ICU.
    joiner = " OR " if args.any else " AND "
    tests = ["(" + joiner.join("instr(fold(body), ?) > 0" for _ in words) + ")"]
    params = [w.casefold() for w in words]
    for key in ("actor", "session"):
        value = getattr(args, key)
        if value:
            tests.append(key + " = ?")
            params.append(value)
    sql = ("SELECT id, source, session, record, selector, actor, kind, timestamp, "
           "substr(body,1,360) AS excerpt FROM items WHERE " + " AND ".join(tests)
           + " ORDER BY timestamp DESC, source, record, selector LIMIT ?")
    return [dict(row) for row in con.execute(sql, [*params, args.limit])]


def query(con, args):
    if args.action == "import":
        return import_file(con, args.file)
    if args.action == "search":
        return search(con, args)
    if args.action == "status":
        return [dict(row) for row in con.execute(
            "SELECT source, actor, kind, count(*) AS blocks FROM items GROUP BY source,actor,kind ORDER BY source,actor")]
    if args.action == "remember":
        if not args.text.strip():
            raise ValueError("Пустая заметка")
        key = uuid.uuid4().hex
        with con:
            con.execute("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?)", (
                key, "candidate-journal", "local-notes", 0, key, "assistant", "candidate",
                datetime.now(timezone.utc).isoformat(), args.text))
        return {"id": key, "kind": "candidate", "actor": "assistant"}
    row = con.execute("SELECT * FROM items WHERE id = ?", (args.id,)).fetchone()
    if row is None:
        raise ValueError("Запись с таким id не найдена")
    if args.action == "get":
        return dict(row)
    if row["kind"] == "candidate":
        return [dict(row)]
    return [dict(item) for item in con.execute(
        "SELECT * FROM items WHERE source=? AND session=? AND record BETWEEN ? AND ? ORDER BY record, selector",
        (row["source"], row["session"], row["record"] - args.radius, row["record"] + args.radius))]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    sub = ap.add_subparsers(dest="action", required=True)
    sub.add_parser("import").add_argument("file", type=Path)
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--any", action="store_true")
    p.add_argument("--actor", choices=["user", "assistant"])
    p.add_argument("--session")
    p.add_argument("--limit", type=int, default=12)
    sub.add_parser("get").add_argument("id")
    t = sub.add_parser("timeline")
    t.add_argument("id")
    t.add_argument("--radius", type=int, default=3)
    sub.add_parser("status")
    sub.add_parser("remember").add_argument("text")
    args = ap.parse_args()
    if getattr(args, "limit", 1) < 1 or getattr(args, "radius", 0) < 0:
        ap.error("Недопустимая граница выборки")
    db = args.db.expanduser().resolve()
    write = args.action in ("import", "remember")
    if not write and not db.is_file():
        ap.error("Индекс не создан. Сначала импортируйте выбранный JSONL.")
    if write:
        os.umask(0o077)
        db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db.as_uri() + ("?mode=rwc" if write else "?mode=ro"), uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.create_function("fold", 1, str.casefold)
        identifier = con.execute("PRAGMA application_id").fetchone()[0]
        if identifier != APP_ID:
            populated = con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            if identifier or populated or not write:
                raise ValueError("Этот файл не является индексом shared-agent-memory")
            con.executescript("BEGIN; " + SCHEMA + f" PRAGMA application_id={APP_ID}; COMMIT;")
        result = query(con, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, sqlite3.Error) as exc:
        sys.exit(str(exc))
