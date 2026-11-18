#!/usr/bin/env python3
"""Local idea journal. The database path is always explicit."""
import argparse
from contextlib import closing
from datetime import date
import json
from pathlib import Path
import sqlite3
import sys


SCHEMA = (
    "CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, issue_key TEXT NOT NULL UNIQUE, "
    "issue_date TEXT NOT NULL, trigger TEXT NOT NULL, payload TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ideas (id TEXT PRIMARY KEY NOT NULL, "
    "run_id INTEGER NOT NULL REFERENCES runs(id), title TEXT NOT NULL, locators TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS marks (seq INTEGER PRIMARY KEY, id TEXT NOT NULL REFERENCES ideas(id), "
    "choice TEXT NOT NULL CHECK(choice IN ('take','no','later')), "
    "after_run INTEGER NOT NULL REFERENCES runs(id), marked_on TEXT NOT NULL)",
)
DB_ID = 0x49444541


def iso_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("expected YYYY-MM-DD")
    return value


def text_field(value):
    return isinstance(value, str) and bool(value.strip())


def read_issue(path):
    issue = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(issue, dict):
        raise ValueError("issue must be an object")
    if set(issue) != {"key", "date", "trigger", "period", "sources", "ideas"}:
        raise ValueError("issue requires key, date, trigger, period, sources and ideas")
    iso_day(issue["date"])
    if not text_field(issue["key"]) or not text_field(issue["period"]):
        raise ValueError("key and period must be nonempty strings")
    if issue["trigger"] not in ("phrase", "schedule", "probe", "retro"):
        raise ValueError("unsupported trigger")
    sources = issue["sources"]
    if not isinstance(sources, dict) or not sources or not all(
        text_field(key) and text_field(value) for key, value in sources.items()
    ):
        raise ValueError("record the observed coverage of each source")
    if not isinstance(issue["ideas"], list):
        raise ValueError("ideas must be an array")
    for idea in issue["ideas"]:
        if not isinstance(idea, dict) or set(idea) != {"move", "title", "locators"}:
            raise ValueError("idea requires move, title and locators")
        if type(idea["move"]) is not int or idea["move"] not in (1, 2) or not text_field(idea["title"]):
            raise ValueError("idea requires move 1 or 2 and a title")
        locators = idea["locators"]
        if not isinstance(locators, list) or not locators or not all(map(text_field, locators)):
            raise ValueError("idea requires at least one nonempty locator")
    return issue


def prepare(db, allow_init):
    marker = db.execute("PRAGMA application_id").fetchone()[0]
    if marker == DB_ID:
        return
    existing = db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    if existing or marker != 0 or not allow_init:
        raise ValueError("file is not an idea-scout journal")
    for statement in SCHEMA:
        db.execute(statement)
    db.execute(f"PRAGMA application_id = {DB_ID}")


def record(db, issue):
    payload = json.dumps(issue, ensure_ascii=False, sort_keys=True)
    prior = db.execute("SELECT id,payload FROM runs WHERE issue_key=?", (issue["key"],)).fetchone()
    if prior:
        if prior[1] != payload:
            raise ValueError("issue key already identifies different content")
        return [row[0] for row in db.execute("SELECT id FROM ideas WHERE run_id=? ORDER BY rowid", (prior[0],))]
    run_id = db.execute(
        "INSERT INTO runs(issue_key,issue_date,trigger,payload) VALUES (?,?,?,?)",
        (issue["key"], issue["date"], issue["trigger"], payload),
    ).lastrowid
    previous_count = db.execute(
        "SELECT count(*) FROM ideas JOIN runs ON ideas.run_id=runs.id WHERE runs.issue_date=?",
        (issue["date"],),
    ).fetchone()[0]
    ids = []
    for number, idea in enumerate(issue["ideas"], start=previous_count + 1):
        idea_id = f"ID-{issue['date'].replace('-', '')}-{number}"
        db.execute("INSERT INTO ideas(id,run_id,title,locators) VALUES (?,?,?,?)",
                   (idea_id, run_id, idea["title"], json.dumps(idea["locators"], ensure_ascii=False)))
        ids.append(idea_id)
    return ids


def mark(db, idea_id, choice):
    if not db.execute("SELECT 1 FROM ideas WHERE id=?", (idea_id,)).fetchone():
        raise ValueError("unknown idea id")
    at_run = db.execute("SELECT max(id) FROM runs").fetchone()[0]
    prior = db.execute("SELECT choice,after_run FROM marks WHERE id=? ORDER BY seq DESC LIMIT 1",
                       (idea_id,)).fetchone()
    if prior != (choice, at_run):
        db.execute("INSERT INTO marks(id,choice,after_run,marked_on) VALUES (?,?,?,?)",
                   (idea_id, choice, at_run, date.today().isoformat()))
    return {"id": idea_id, "mark": choice}


def status(db):
    run_rows = list(db.execute("SELECT id,payload FROM runs ORDER BY id"))
    runs = [dict(json.loads(payload), number=n) for n, payload in run_rows]
    normal = [r["number"] for r in runs if r["trigger"] != "retro"]
    last = {}
    latest_take = 0
    for idea_id, choice, after_run in db.execute("SELECT id,choice,after_run FROM marks ORDER BY seq"):
        last[idea_id] = (choice, after_run)
        if choice == "take":
            latest_take = after_run
    ideas = []
    for idea_id, title, locators in db.execute("SELECT id,title,locators FROM ideas ORDER BY rowid"):
        choice, after_run = last.get(idea_id, (None, 0))
        ideas.append({"id": idea_id, "title": title, "locators": json.loads(locators), "mark": choice,
                      "reconsider": choice == "later" and sum(n > after_run for n in normal) >= 4})
    return {"runs": [{k: v for k, v in r.items() if k != "ideas"} for r in runs], "ideas": ideas,
            "review_frequency": sum(n > latest_take for n in normal) >= 4}


def action(args):
    writing = args.command in ("record", "mark")
    issue = read_issue(args.input) if args.command == "record" else None
    mode = "rwc" if args.command == "record" else "rw" if writing else "ro"
    uri = args.db.resolve().as_uri() + "?mode=" + mode
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE" if writing else "BEGIN")
        prepare(db, allow_init=args.command == "record")
        if args.command == "record":
            return {"ids": record(db, issue)}
        if args.command == "mark":
            return mark(db, args.idea_id, args.choice)
        if args.command == "status":
            return status(db)
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("journal integrity or links failed")
        return {"integrity": "ok"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("record").add_argument("--input", type=Path, required=True)
    decision = commands.add_parser("mark")
    decision.add_argument("idea_id")
    decision.add_argument("choice", choices=["take", "no", "later"])
    commands.add_parser("status")
    commands.add_parser("check")
    args = parser.parse_args()
    try:
        result = action(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
