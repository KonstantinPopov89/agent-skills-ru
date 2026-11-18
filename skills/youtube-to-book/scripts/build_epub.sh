#!/usr/bin/env bash
# Build and verify an EPUB in the book's directory. Keep previous revisions.
set -eu
book=${1:?usage: build_epub.sh BOOK.md [OUTPUT.epub]}
[[ -s "$book" ]] || { echo 'Book file missing or empty' >&2; exit 2; }
dir="$(cd "$(dirname "$book")" && pwd)"
book_name="$(basename "$book")"
out="${2:-$dir/book.epub}"
[[ ! -e "$out" ]] || { echo 'Choose a new EPUB path' >&2; exit 1; }
css="$(cd "$(dirname "$0")/../assets" && pwd)/epub.css"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
(cd "$dir" && pandoc --from=markdown --to=epub3 --toc --toc-depth=2 --split-level=1 \
  --css="$css" -o "$tmp_dir/book.epub" -- "$book_name")
python3 - "$book" "$tmp_dir/book.epub" "$out" <<'PY'
from pathlib import Path
from shutil import copyfileobj
from xml.etree import ElementTree as ET
from zipfile import ZipFile
import json, posixpath, subprocess, sys
book, built, out = map(Path, sys.argv[1:])
parsed = json.loads(subprocess.check_output(['pandoc', '--from=markdown', '--to=json', '--', str(book.resolve())]))
expected = sum(b['t'] == 'Header' and b['c'][0] == 1 for b in parsed['blocks'])
with ZipFile(built) as z:
    if z.testzip() or not expected:
        raise SystemExit('Invalid EPUB or book has no H1 chapters')
    manifest_path = ET.fromstring(z.read('META-INF/container.xml')).find('.//{*}rootfile').get('full-path')
    opf = ET.fromstring(z.read(manifest_path))
    actual = 0
    for item in opf.findall('.//{*}manifest/{*}item'):
        if item.get('media-type') != 'application/xhtml+xml' or 'nav' in item.get('properties', '').split():
            continue
        doc = ET.fromstring(z.read(posixpath.normpath(posixpath.join(posixpath.dirname(manifest_path), item.get('href')))))
        actual += sum('title' not in node.get('class', '').split() for node in doc.findall('.//{*}h1'))
    if actual != expected:
        raise SystemExit(f'Chapter mismatch: {expected} in Markdown, {actual} in EPUB')
with built.open('rb') as src, out.open('xb') as dst:
    copyfileobj(src, dst)
print(f'{out}: {actual} chapter(s)')
PY
