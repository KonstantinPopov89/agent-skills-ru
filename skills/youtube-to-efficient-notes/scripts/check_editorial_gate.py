#!/usr/bin/env python3
"""Check review completeness and exact file binding, not semantic truth."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

CHECKS = ('meaning', 'examples', 'caveats', 'stories', 'attribution',
          'voice_boundaries', 'numbers', 'causality')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def run(args):
    errors = []
    bindings = {}
    if not args.review.is_file():
        return {'status': 'FAIL', 'errors': ['REVIEW_MISSING']}
    try:
        review = json.loads(args.review.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        return {'status': 'FAIL', 'errors': ['REVIEW_UNREADABLE: ' + str(exc)]}
    if not isinstance(review, dict):
        return {'status': 'FAIL', 'errors': ['REVIEW_NOT_OBJECT']}
    if review.get('schema_version') != 1:
        errors.append('SCHEMA_VERSION')
    author, reviewer = review.get('author_id'), review.get('reviewer_id')
    if not nonempty(author) or not nonempty(reviewer) or author.strip() == reviewer.strip():
        errors.append('INDEPENDENT_REVIEWER_REQUIRED')
    if review.get('verdict') != 'PASS':
        errors.append('REVIEW_NOT_PASS')
    if review.get('full_source') is not True:
        errors.append('FULL_SOURCE_REVIEW_REQUIRED')
    checks = review.get('checks')
    if not isinstance(checks, dict) or any(checks.get(k) is not True for k in CHECKS):
        errors.append('SEMANTIC_CHECKS_INCOMPLETE')

    expected_sources = {}
    for path in args.source:
        if not path.is_file():
            errors.append('SOURCE_MISSING: ' + str(path))
        else:
            key = str(path.resolve())
            if key in expected_sources:
                errors.append('DUPLICATE_SOURCE_ARGUMENT')
            expected_sources[key] = sha(path)
    actual_sources = {}
    sources = review.get('sources')
    if isinstance(sources, list):
        for entry in sources:
            if not isinstance(entry, dict) or not nonempty(entry.get('path')):
                errors.append('SOURCE_BINDING_INVALID')
                continue
            key = str(Path(entry['path']).resolve())
            if key in actual_sources:
                errors.append('DUPLICATE_SOURCE_BINDING')
            actual_sources[key] = entry.get('sha256')
    if not expected_sources or actual_sources != expected_sources:
        errors.append('SOURCE_BINDING_MISMATCH')
    bindings['sources'] = expected_sources

    notes_hash = None
    if not args.notes.is_file():
        errors.append('NOTES_MISSING')
    else:
        notes_hash = sha(args.notes)
        notes_entry = review.get('notes')
        if not isinstance(notes_entry, dict):
            errors.append('NOTES_BINDING_MISSING')
        elif not nonempty(notes_entry.get('path')) or str(Path(notes_entry['path']).resolve()) != str(args.notes.resolve()) or notes_entry.get('sha256') != notes_hash:
            errors.append('NOTES_BINDING_MISMATCH')
    bindings['notes'] = {'path': str(args.notes.resolve()), 'sha256': notes_hash}

    coverage = review.get('coverage')
    ids = set()
    if not isinstance(coverage, list) or not coverage:
        errors.append('COVERAGE_MISSING')
        coverage = []
    for row in coverage:
        if not isinstance(row, dict) or not nonempty(row.get('id')) or not nonempty(row.get('source_locator')):
            errors.append('COVERAGE_ROW_INVALID')
            continue
        if row['id'] in ids:
            errors.append('COVERAGE_ID_DUPLICATE: ' + row['id'])
        ids.add(row['id'])
        status = row.get('status')
        if status == 'PRESERVED':
            if not nonempty(row.get('target_locator')):
                errors.append('COVERAGE_TARGET_MISSING: ' + row['id'])
        elif status == 'DUPLICATE':
            if not nonempty(row.get('duplicate_of')) or not nonempty(row.get('target_locator')):
                errors.append('DUPLICATE_REFERENCE_MISSING: ' + row['id'])
        elif status == 'SERVICE_OMISSION':
            if not nonempty(row.get('reason')):
                errors.append('SERVICE_REASON_MISSING: ' + row['id'])
        else:
            errors.append('COVERAGE_UNRESOLVED: ' + row['id'])
    for row in coverage:
        if isinstance(row, dict) and row.get('status') == 'DUPLICATE' and (row.get('duplicate_of') not in ids or row.get('duplicate_of') == row.get('id')):
            errors.append('DUPLICATE_REFERENCE_INVALID: ' + str(row.get('id')))
        elif isinstance(row, dict) and row.get('status') == 'DUPLICATE':
            target = next((r for r in coverage if isinstance(r, dict) and r.get('id') == row.get('duplicate_of')), {})
            if target.get('status') != 'PRESERVED':
                errors.append('DUPLICATE_MUST_POINT_TO_PRESERVED: ' + str(row.get('id')))

    findings = review.get('findings')
    if not isinstance(findings, list):
        errors.append('FINDINGS_LIST_MISSING')
        findings = []
    for finding in findings:
        if not isinstance(finding, dict) or finding.get('status') != 'CLOSED':
            errors.append('UNCLOSED_FINDING')
            continue
        if any(not nonempty(finding.get(k)) for k in ('id', 'issue', 'source_locator', 'draft_locator', 'repair', 'final_locator')):
            errors.append('FINDING_HISTORY_INCOMPLETE')
        if finding.get('verified_notes_sha256') != notes_hash:
            errors.append('FINDING_VERIFIED_ON_OTHER_TEXT')

    if args.html:
        if not args.html.is_file():
            errors.append('HTML_MISSING')
        elif notes_hash is not None:
            if not args.pandoc:
                errors.append('PANDOC_MISSING_FOR_HTML_CHECK')
            else:
                try:
                    fragment = subprocess.run(
                        [args.pandoc, '--from=markdown-smart-raw_html-raw_attribute', '--to=html5', '--wrap=none'],
                        input=args.notes.read_text(encoding='utf-8'), capture_output=True,
                        text=True, check=True).stdout
                    document = args.html.read_text(encoding='utf-8')
                    main = re.search(r'<main\b[^>]*\bid="book"[^>]*>(.*?)</main>', document, re.S)
                    body = main.group(1) if main else ''
                    body = re.sub(r'^\s*<header\b[^>]*\bid="title-block-header"[^>]*>.*?</header>', '', body, count=1, flags=re.S)
                    body = re.sub(r'^\s*<details\b[^>]*\bid="reader-toc"[^>]*>.*?</details>', '', body, count=1, flags=re.S)
                    if body.strip() != fragment.strip():
                        errors.append('HTML_NOT_BUILT_FROM_REVIEWED_TEXT')
                except (OSError, subprocess.CalledProcessError) as exc:
                    errors.append('HTML_CHECK_FAILED: ' + str(exc))
            bindings['html'] = {'path': str(args.html.resolve()), 'sha256': sha(args.html)}
    return {'status': 'FAIL' if errors else 'PASS', 'errors': errors,
            'reviewer_id': reviewer, 'coverage_rows': len(coverage),
            'closed_findings': len(findings), 'bindings': bindings,
            'limitation': 'Validates review records and file hashes; semantic accuracy and reviewer independence require real independent review.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, action='append', required=True)
    p.add_argument('--notes', type=Path, required=True)
    p.add_argument('--review', type=Path, required=True)
    p.add_argument('--html', type=Path)
    p.add_argument('--pandoc', default=shutil.which('pandoc'))
    args = p.parse_args()
    try:
        result = run(args)
    except OSError as exc:
        result = {'status': 'FAIL', 'errors': ['FILE_READ_ERROR: ' + str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
