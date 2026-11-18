#!/usr/bin/env python3
"""Build the approved self-contained dark reader from Markdown using Pandoc."""
import argparse
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess


class Headings(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == 'h1':
            self.current = [dict(attrs).get('id', ''), []]

    def handle_data(self, data):
        if self.current is not None:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag == 'h1' and self.current is not None:
            self.items.append((self.current[0], ''.join(self.current[1])))
            self.current = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('markdown', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--title', required=True)
    parser.add_argument('--subtitle', default='Конспект с мини-кейсами')
    parser.add_argument('--source-id', required=True)
    parser.add_argument('--lang', default='ru')
    parser.add_argument('--pandoc', default=shutil.which('pandoc'))
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; use a new path or revision')
    if not args.pandoc:
        parser.error('Pandoc is required')
    if not re.fullmatch(r'[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]+)*', args.lang):
        parser.error('Invalid language tag')
    markdown = args.markdown.read_text(encoding='utf-8')
    fragment = subprocess.run(
        [args.pandoc, '--from=markdown-smart-raw_html-raw_attribute',
         '--to=html5', '--wrap=none'],
        input=markdown, capture_output=True, text=True, check=True).stdout
    headings = Headings()
    headings.feed(fragment)
    if not headings.items or any(not i for i, _ in headings.items):
        parser.error('Markdown must contain chapter headings at level 1')
    ids = [i for i, _ in headings.items]
    if len(ids) != len(set(ids)):
        parser.error('Duplicate chapter IDs')
    toc = '<nav id="TOC" role="doc-toc"><ul>' + ''.join(
        '<li><a href="#' + html.escape(i, quote=True) + '">' + html.escape(t) + '</a></li>'
        for i, t in headings.items) + '</ul></nav>'
    storage = json.dumps('reader:' + args.source_id + ':font-size', ensure_ascii=True)
    storage = storage.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    values = {
        'LANG': html.escape(args.lang, quote=True),
        'TITLE': html.escape(args.title),
        'SUBTITLE': html.escape(args.subtitle),
        'TOC_LABEL': 'Оглавление · ' + str(len(headings.items)),
        'TOC': toc,
        'CONTENT': fragment,
        'STORAGE_KEY_JSON': storage,
    }
    template = (Path(__file__).resolve().parents[1] / 'assets/reader-template.html').read_text(encoding='utf-8')
    result = re.sub(r'@@([A-Z_]+)@@', lambda m: values[m.group(1)], template)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as file:
        file.write(result)
    print(json.dumps({'output': str(args.output.resolve()), 'chapters': len(ids)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
