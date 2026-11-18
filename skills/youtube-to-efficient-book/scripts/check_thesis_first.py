#!/usr/bin/env python3
"""Check the thesis-first reading order in an efficient Markdown book and EPUB."""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


DEFAULT_LABELS = ("Тезис", "Thesis")
LETTER = re.compile(r"[^\W\d_]")


def thesis_patterns(labels: list[str]) -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
    """Markdown first line, EPUB first paragraph, any thesis line: for the given label words."""
    alt = "|".join(re.escape(label) for label in labels)
    return (
        re.compile(rf"^(?:\*\*(?:{alt}):\*\*|(?:{alt}):)\s+\S", re.IGNORECASE),
        re.compile(rf"^(?:{alt}):\s+\S", re.IGNORECASE),
        re.compile(rf"^(?:{alt}):", re.IGNORECASE),
    )
CHAPTER = re.compile(r"^# (.+)$", re.MULTILINE)
EPUB_CHAPTER = re.compile(r"(?:^|/)ch\d+\.xhtml$")


def markdown_chapters(path: Path, labels: list[str]) -> tuple[int, list[str]]:
    THESIS, _, _ = thesis_patterns(labels)
    expected = " / ".join(f"«{label}:»" for label in labels)
    content = path.read_text(encoding="utf-8")
    if content.startswith("---\n"):
        parts = content.split("\n---\n", 1)
        if len(parts) == 2:
            content = parts[1]
    headings = list(CHAPTER.finditer(content))
    errors: list[str] = []
    if not headings:
        return 0, ["Markdown: не найдены главы H1"]
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        lines = [line.strip() for line in content[heading.end():end].splitlines() if line.strip()]
        label = f"Markdown, глава {index + 1} ({heading.group(1)})"
        if not lines or not THESIS.match(lines[0]):
            errors.append(f"{label}: первый абзац должен начинаться с {expected}")
        if len(lines) < 2 or not any(
            LETTER.search(line)
            and not line.startswith(("#", "!", ">"))
            and not THESIS.match(line)
            for line in lines[1:]
        ):
            errors.append(f"{label}: после тезиса нет объяснения")
    return len(headings), errors


def epub_chapters(path: Path, labels: list[str]) -> tuple[int, list[str]]:
    _, FIRST, ANY = thesis_patterns(labels)
    expected = " / ".join(f"«{label}:»" for label in labels)
    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as book:
            broken = book.testzip()
            if broken:
                return 0, [f"EPUB: повреждён файл {broken}"]
            names = sorted(name for name in book.namelist() if EPUB_CHAPTER.search(name))
            if not names:
                return 0, ["EPUB: не найдены файлы глав chNNN.xhtml"]
            for index, name in enumerate(names, 1):
                try:
                    root = ET.fromstring(book.read(name))
                except ET.ParseError as exc:
                    errors.append(f"EPUB, глава {index}: неверный XML ({exc})")
                    continue
                body = next((el for el in root.iter() if el.tag.rsplit("}", 1)[-1] == "body"), None)
                paragraphs = [] if body is None else [
                    "".join(el.itertext()).strip()
                    for el in body.iter()
                    if el.tag.rsplit("}", 1)[-1] == "p"
                ]
                if not paragraphs or not FIRST.match(paragraphs[0]):
                    errors.append(f"EPUB, глава {index}: первый абзац должен начинаться с {expected}")
                if len(paragraphs) < 2 or not any(
                    LETTER.search(p) and not ANY.match(p)
                    for p in paragraphs[1:]
                ):
                    errors.append(f"EPUB, глава {index}: после тезиса нет объяснения")
    except (OSError, zipfile.BadZipFile) as exc:
        return 0, [f"EPUB: не удалось прочитать архив ({exc})"]
    return len(names), errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("epub", type=Path)
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        help="слово «тезис» на языке книги, если это не «Тезис» и не «Thesis» (можно несколько раз)",
    )
    args = parser.parse_args()
    labels = list(DEFAULT_LABELS) + [label.strip().rstrip(":") for label in args.label if label.strip()]
    md_count, md_errors = markdown_chapters(args.markdown, labels)
    epub_count, epub_errors = epub_chapters(args.epub, labels)
    errors = md_errors + epub_errors
    if md_count != epub_count:
        errors.append(f"Число глав различается: Markdown {md_count}, EPUB {epub_count}")
    for error in errors:
        print(f"FAIL: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"THESIS_FIRST_OK: {md_count} глав в Markdown и EPUB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
