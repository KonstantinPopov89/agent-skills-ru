#!/usr/bin/env python3
"""Universal CLI compiler and auditor for the Minimalist Book PDF Standard (Typst).

Supports both Markdown (.md) and Word (.docx) inputs with automatic Russian
micro-typography normalization, epigraph/riddle blocks, pull-quotes, and author notes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
TEMPLATE_TYP = SKILL_ROOT / "assets" / "minimalist-book.typ"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def clean_typography(text: str) -> str:
    """Apply Russian editorial micro-typography rules."""
    text = re.sub(r"([а-яёa-z])\.([А-ЯЁA-Z])", r"\1. \2", text)
    text = re.sub(r"»«", r"» — «", text)
    text = re.sub(r"\s+-\s+", " — ", text)
    out = []
    open_q = True
    for ch in text:
        if ch in ('"', "“", "”"):
            if ch == "“":
                out.append("«")
            elif ch == "”":
                out.append("»")
            else:
                out.append("«" if open_q else "»")
                open_q = not open_q
        else:
            out.append(ch)
    text = "".join(out)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def escape_typst(text: str) -> str:
    """Pass source characters as a string, outside the Typst markup parser."""
    return "#text(" + json.dumps(text, ensure_ascii=False) + ")"


def format_inline_md(text: str) -> str:
    """Render the supported non-nested spans; other text remains literal."""
    parts = []
    last = 0
    spans = re.compile(r"`([^`]+)`|\*\*(.+?)\*\*|\*([^*]+)\*|\[([^\]]+)\]\((https?://[^)\s]+)\)")
    for hit in spans.finditer(text):
        if hit.start() > last:
            parts.append(escape_typst(text[last:hit.start()]))
        code, bold, italic, label, url = hit.groups()
        if code is not None:
            parts.append("#raw(" + json.dumps(code, ensure_ascii=False) + ")")
        elif label is not None:
            parts.append("#link(" + json.dumps(url, ensure_ascii=False) + ")[" + escape_typst(label) + "]")
        else:
            markup = "strong" if bold is not None else "emph"
            parts.append("#" + markup + "[" + escape_typst(bold or italic) + "]")
        last = hit.end()
    if last < len(text):
        parts.append(escape_typst(text[last:]))
    return "".join(parts)


def parse_markdown(md_text: str) -> tuple[dict, list[str]]:
    """Parse optional YAML frontmatter and Markdown blocks into Typst calls."""
    meta: dict = {}
    lines = md_text.splitlines()
    idx = 0
    if lines and lines[0].strip() == "---":
        idx = 1
        authors: list[str] = []
        while idx < len(lines) and lines[idx].strip() != "---":
            line = lines[idx].strip()
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k == "author" or k == "authors":
                    if v:
                        authors.extend([x.strip() for x in v.split(",") if x.strip()])
                else:
                    meta[k] = v
            elif line.startswith("- "):
                authors.append(line[2:].strip().strip('"').strip("'"))
            idx += 1
        if authors:
            meta["authors"] = authors
        idx += 1

    body_out: list[str] = []
    ch_counter = 0
    in_quote: list[str] = []
    quote_kind = "riddle"
    in_code: list[str] = []
    is_in_code = False

    def flush_quote() -> None:
        nonlocal in_quote, quote_kind
        if not in_quote:
            return
        joined = "\n\n".join(format_inline_md(x) for x in in_quote if x.strip())
        if quote_kind == "pull":
            body_out.append(f"#pull-quote[\n{joined}\n]\n")
        elif quote_kind == "note":
            body_out.append(f"#author-note[\n{joined}\n]\n")
        else:
            body_out.append(f"#riddle-box[\n{joined}\n]\n")
        in_quote = []
        quote_kind = "riddle"

    for raw_line in lines[idx:]:
        if raw_line.strip().startswith("```"):
            flush_quote()
            if not is_in_code:
                is_in_code = True
                in_code = []
            else:
                is_in_code = False
                code_str = "\n".join(in_code)
                body_out.append(
                    f'#block(width: 100%, fill: card-bg, inset: 9pt, radius: 3pt, stroke: 0.4pt + border-subtle, breakable: false)[#set text(font: "DejaVu Sans Mono", size: 8.1pt); #raw(block: true, {json.dumps(code_str, ensure_ascii=False)})]\n'
                )
            continue
        if is_in_code:
            in_code.append(raw_line)
            continue

        s = raw_line.strip()
        if not s:
            flush_quote()
            body_out.append("\n")
            continue

        if s.startswith(">"):
            content = s.lstrip(">").strip()
            if content.lower().startswith("[!quote]"):
                quote_kind = "pull"
                content = content[8:].strip()
            elif content.lower().startswith("[!note]"):
                quote_kind = "note"
                content = content[7:].strip()
            elif content.lower().startswith("[!epigraph]") or content.lower().startswith("[!riddle]"):
                quote_kind = "riddle"
                content = re.sub(r"^\[![a-zA-Z]+\]", "", content).strip()
            if content:
                in_quote.append(content)
            continue
        else:
            flush_quote()

        if s in ("***", "---", "___"):
            body_out.append("#scene-break()\n")
            continue

        if s.startswith("[") and s.endswith("]") and len(s) > 4 and "](" not in s:
            body_out.append(f"#author-note[{format_inline_md(s[1:-1].strip())}]\n")
            continue

        if s.startswith("# "):
            title = s[2:].strip().rstrip(".")
            if title.lower() in ("предисловие", "введение", "пролог", "эпилог", "заключение", "приложения"):
                body_out.append(f"#chapter-heading(none, [{format_inline_md(title)}])\n")
            else:
                ch_counter += 1
                m = re.match(r"^(?:Глава|Раздел)\s+(\d+)\.?\s*(.+)$", title, re.IGNORECASE)
                if m:
                    num_str = f"{int(m.group(1)):02d}"
                    clean_t = m.group(2).strip()
                else:
                    num_str = f"{ch_counter:02d}"
                    clean_t = title
                body_out.append(f'#chapter-heading("{num_str}", [{format_inline_md(clean_t)}])\n')
            continue

        if s.startswith("## "):
            h2 = s[3:].strip().rstrip(".")
            body_out.append(f"== {format_inline_md(h2)}\n")
            continue

        if s.startswith("### "):
            h3 = s[4:].strip().rstrip(".")
            body_out.append(f"=== {format_inline_md(h3)}\n")
            continue

        if s.startswith("- ") or s.startswith("* "):
            body_out.append(f"- {format_inline_md(s[2:].strip())}\n")
            continue

        m_num = re.match(r"^(\d+)\.\s+(.+)$", s)
        if m_num:
            body_out.append(f"+ {format_inline_md(m_num.group(2).strip())}\n")
            continue

        body_out.append(f"{format_inline_md(s)}\n\n")

    if is_in_code:
        raise ValueError("Unclosed Markdown code fence")
    flush_quote()
    return meta, body_out


def parse_docx(docx_path: pathlib.Path) -> tuple[dict, list[str]]:
    """Parse a Word (.docx) file into Typst blocks following the Minimalist Book Standard."""
    with zipfile.ZipFile(docx_path) as zf:
        xml_bytes = zf.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    body = root.find(f"{W}body")

    if body is None:
        raise ValueError("No Word document body")
    unsupported = ("tbl", "drawing", "hyperlink", "footnoteReference", "endnoteReference", "ins", "del", "fldSimple", "fldChar")
    if any(body.find(".//" + W + tag) is not None for tag in unsupported):
        raise ValueError("This DOCX contains tables, images, links, revisions or fields. Use a full document converter or a manual Typst layout.")
    paras = []
    for p in body.findall(f"{W}p"):
        pPr = p.find(f"{W}pPr")
        style = None
        is_list = False
        if pPr is not None:
            st = pPr.find(f"{W}pStyle")
            if st is not None:
                style = st.attrib.get(f"{W}val")
            if pPr.find(f"{W}numPr") is not None:
                is_list = True
        runs = []
        for r in p.findall(f"{W}r"):
            rt = "".join(t.text for t in r.findall(f"{W}t") if t.text)
            if not rt:
                continue
            rPr = r.find(f"{W}rPr")
            b = rPr is not None and rPr.find(f"{W}b") is not None
            i = rPr is not None and rPr.find(f"{W}i") is not None
            if runs and runs[-1][1] == b and runs[-1][2] == i:
                runs[-1][0] += rt
            else:
                runs.append([rt, b, i])
        raw = "".join(x[0] for x in runs).strip()
        if raw:
            paras.append({"style": style, "is_list": is_list, "runs": runs, "clean": clean_typography(raw)})

    meta: dict = {}
    body_out: list[str] = []
    ch_counter = 0
    start_idx = 0
    if paras and paras[0]["style"] == "Title":
        meta["title"] = paras[0]["clean"]
        start_idx = 1

    for p in paras[start_idx:]:
        txt = p["clean"]
        st = p["style"]
        if txt in ("***", "---", "___"):
            body_out.append("#scene-break()\n")
        elif txt.startswith("[") and txt.endswith("]"):
            body_out.append(f"#author-note[{escape_typst(txt[1:-1].strip())}]\n")
        elif st == "Heading1":
            if txt.lower() in ("предисловие", "введение", "оглавление", "содержание"):
                body_out.append(f"#chapter-heading(none, [{escape_typst(txt.rstrip('.'))}])\n")
            else:
                ch_counter += 1
                body_out.append(f'#chapter-heading("{ch_counter:02d}", [{escape_typst(txt.rstrip(".")) }])\n')
        elif st == "Heading2":
            body_out.append(f"== {escape_typst(txt.rstrip('.'))}\n")
        elif st == "Heading3":
            body_out.append(f"=== {escape_typst(txt.rstrip('.'))}\n")
        elif p["is_list"]:
            body_out.append(f"- {escape_typst(txt)}\n")
        else:
            spans: list[str] = []
            for content, bold, italic in p["runs"]:
                part = escape_typst(content)
                if italic:
                    part = "#emph[" + part + "]"
                if bold:
                    part = "#strong[" + part + "]"
                spans.append(part)
            body_out.append("".join(spans) + "\n\n")

    return meta, body_out


def cmd_build(args: argparse.Namespace) -> int:
    in_path = pathlib.Path(args.input).resolve()
    out_pdf = pathlib.Path(args.output).resolve()
    if not in_path.exists():
        print(f"ERROR: input not found: {in_path}", file=sys.stderr)
        return 1

    if in_path == out_pdf or args.keep_typ and pathlib.Path(args.keep_typ).resolve() in (in_path, out_pdf):
        print("Input, output and intermediate paths must be distinct.", file=sys.stderr)
        return 2
    try:
        if in_path.suffix.lower() == ".docx":
            meta, blocks = parse_docx(in_path)
        elif in_path.suffix.lower() in (".md", ".markdown"):
            source = in_path.read_text(encoding="utf-8")
            if re.search(r"(?m)^\s*\|?\s*:?-{3,}:?\s*\|\s*:?-{3,}|!\[[^\]]*\]\(", source):
                raise ValueError("Markdown images and tables require manual layout or a full document converter.")
            meta, blocks = parse_markdown(source)
        else:
            raise ValueError("Expected Markdown or DOCX input")
    except (ValueError, OSError, zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
        print(f"Cannot convert input: {exc}", file=sys.stderr)
        return 2

    title = args.title or meta.get("title") or in_path.stem
    subtitle = args.subtitle or meta.get("subtitle")
    rubric = args.rubric or meta.get("rubric") or "ДОКУМЕНТАЛЬНОЕ ИЗДАНИЕ"
    edition_note = args.edition_note or meta.get("edition_note") or "АВТОРСКОЕ ИЗДАНИЕ"
    authors = args.author or meta.get("authors") or []
    show_toc = not args.no_toc

    template_src = TEMPLATE_TYP.read_text(encoding="utf-8")
    authors_typ = "(" + ", ".join(f"{json.dumps(a, ensure_ascii=False)}" for a in authors) + ("," if len(authors) == 1 else "") + ")"
    subtitle_typ = json.dumps(subtitle, ensure_ascii=False) if subtitle else "none"

    typ_doc = (
        template_src
        + f"""
#show: minimalist-book.with(
  title: {json.dumps(title, ensure_ascii=False)},
  subtitle: {subtitle_typ},
  authors: {authors_typ},
  rubric: {json.dumps(rubric, ensure_ascii=False)},
  edition-note: {json.dumps(edition_note, ensure_ascii=False)},
  show-toc: {"true" if show_toc else "false"},
)

"""
        + "".join(blocks)
    )

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    compile_cmd = ["typst", "compile", "--input", f"serif={args.serif}", "--input", f"sans={args.sans}"]
    if args.keep_typ:
        typ_path = pathlib.Path(args.keep_typ).resolve()
        typ_path.parent.mkdir(parents=True, exist_ok=True)
        typ_path.write_text(typ_doc, encoding="utf-8")
        res = subprocess.run([*compile_cmd, str(typ_path), str(out_pdf)], capture_output=True, text=True)
    else:
        with tempfile.NamedTemporaryFile(suffix=".typ", mode="w", encoding="utf-8", delete=False) as tf:
            tf.write(typ_doc)
            tf_path = pathlib.Path(tf.name)
        try:
            res = subprocess.run([*compile_cmd, str(tf_path), str(out_pdf)], capture_output=True, text=True)
        finally:
            tf_path.unlink(missing_ok=True)

    if res.stderr.strip():
        print(res.stderr, file=sys.stderr)
    if res.returncode != 0:
        return res.returncode

    print(json.dumps({"status": "OK", "output_pdf": str(out_pdf), "bytes": out_pdf.stat().st_size}, ensure_ascii=False))
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    pdf_path = pathlib.Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(json.dumps({"status": "FAIL", "error": f"PDF not found: {pdf_path}"}))
        return 1
    data = pdf_path.read_bytes()
    checks = {
        "valid_pdf_header": data.startswith(b"%PDF-"),
        "non_empty": len(data) > 4096,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    ok = checks["valid_pdf_header"] and checks["non_empty"]
    print(json.dumps({"status": "PASS" if ok else "FAIL", "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimalist Book PDF Standard CLI (Typst)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Compile Markdown or DOCX into a Minimalist Book PDF")
    b.add_argument("--input", "-i", required=True, help="Input .md or .docx file")
    b.add_argument("--output", "-o", required=True, help="Output .pdf path")
    b.add_argument("--title", help="Book title override")
    b.add_argument("--subtitle", help="Book subtitle override")
    b.add_argument("--author", action="append", help="Author name (can be passed multiple times)")
    b.add_argument("--rubric", help="Top rubric line on cover")
    b.add_argument("--edition-note", help="Bottom-right edition label on cover")
    b.add_argument("--serif", default="Libertinus Serif", help="Installed body font family")
    b.add_argument("--sans", default="DejaVu Sans Mono", help="Installed navigation font family")
    b.add_argument("--no-toc", action="store_true", help="Omit table of contents")
    b.add_argument("--keep-typ", help="Save intermediate .typ source to this path")

    a = sub.add_parser("audit", help="Verify compiled PDF integrity")
    a.add_argument("--pdf", required=True, help="Path to compiled PDF")

    args = parser.parse_args()
    if args.cmd == "build":
        return cmd_build(args)
    return cmd_audit(args)


if __name__ == "__main__":
    raise SystemExit(main())
