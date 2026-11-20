#!/usr/bin/env python3
"""Prepare source-anchored chunks from documents for the review graph."""
import argparse
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile


def local(name):
    return name.rsplit('}', 1)[-1]


def safe_output(out):
    out = out.expanduser().resolve()
    skill = Path(__file__).resolve().parents[1]
    result = subprocess.run(['git', '-C', str(skill), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    protected = Path(result.stdout.strip()).resolve() if result.returncode == 0 else skill
    if out == protected or protected in out.parents:
        raise ValueError('output must be outside the skill repository')
    if out.exists():
        raise ValueError('output already exists; choose a new directory')
    return out


def clean(text):
    return re.sub(r'[\r\n]+', ' ', str(text)).strip()


def text_events(path):
    for n, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if line.strip():
            yield ('TEXT', f'line[{n}]', line)


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events = []
        self.stack = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)
        if tag in ('script', 'style', 'template'): self.hidden += 1
        if not self.hidden and tag in ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr', 'td', 'th', 'li'):
            self.events.append(('CONTROL', '/'.join(self.stack), tag))
        if not self.hidden and tag == 'img':
            ref = dict(attrs).get('src', '')
            self.events.append(('IMAGE', '/'.join(self.stack), 'external_or_embedded_ref=' + ref))
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'template') and self.hidden: self.hidden -= 1
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]
    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.events.append(('TEXT', '/'.join(self.stack) or 'html', clean(data)))


def docx_events(path, image_dir):
    events = []
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if 'word/document.xml' not in names: raise ValueError('DOCX is missing word/document.xml')
        image_names = [n for n in sorted(names) if n.startswith('word/media/') and not n.endswith('/')]
        for name in image_names:
            (image_dir / Path(name).name).write_bytes(archive.read(name))
        relations = {}
        relfile = 'word/_rels/document.xml.rels'
        if relfile in names:
            for rel in ET.fromstring(archive.read(relfile)):
                relations[rel.attrib.get('Id')] = Path(rel.attrib.get('Target', '')).name
        def walk(node, path_label, row=None, col=None):
            tag = local(node.tag)
            if tag == 'p': events.append(('PARA', path_label, 'paragraph'))
            elif tag == 'tbl': events.append(('TABLE', path_label, 'table'))
            elif tag == 'tr': events.append(('ROW', path_label, f'row={row}'))
            elif tag == 'tc': events.append(('CELL', path_label, f'row={row} col={col}'))
            elif tag == 't':
                if node.text and node.text.strip(): events.append(('TEXT', path_label, clean(node.text)))
                return
            elif tag == 'blip':
                ref = next((v for k, v in node.attrib.items() if local(k) in ('embed', 'link')), '')
                events.append(('IMAGE', path_label, 'ref=' + relations.get(ref, ref)))
            counters = {}
            for child in node:
                child_tag = local(child.tag); counters[child_tag] = counters.get(child_tag, 0) + 1
                next_row = counters[child_tag] - 1 if child_tag == 'tr' else row
                next_col = counters[child_tag] - 1 if child_tag == 'tc' else col
                walk(child, f'{path_label}/{child_tag}[{counters[child_tag]}]', next_row, next_col)
        for filename in ['word/document.xml', 'word/footnotes.xml', 'word/endnotes.xml']:
            if filename in names:
                walk(ET.fromstring(archive.read(filename)), filename)
    return events, image_names


def pdf_events(path):
    exe = shutil.which('pdftotext')
    if not exe: raise ValueError('PDF extraction requires pdftotext; use a compatible reader and --events instead')
    result = subprocess.run([exe, '-layout', '-enc', 'UTF-8', str(path), '-'], capture_output=True, text=True)
    if result.returncode: raise ValueError('pdftotext failed: ' + result.stderr.strip())
    pages = result.stdout.split('\f')
    if pages and not pages[-1].strip(): pages.pop()
    events = []
    for n, page in enumerate(pages, 1):
        events.append(('PAGE', f'page[{n}]', f'physical_page={n}'))
        for line_no, line in enumerate(page.splitlines(), 1):
            if line.strip(): events.append(('TEXT', f'page[{n}]/line[{line_no}]', line.strip()))
    return events


def read_events(path):
    events = []
    for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip(): continue
        record = json.loads(line)
        if not isinstance(record, dict) or not all(isinstance(record.get(k), str) for k in ('kind','path','text')):
            raise ValueError(f'event line {n} requires string kind, path and text')
        if not re.fullmatch('[A-Z_]+', record['kind']) or not record['path'].strip():
            raise ValueError(f'event line {n} has an invalid kind or path')
        events.append((record['kind'], record['path'], record['text']))
    return events


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True, help='original document')
    p.add_argument('--out', required=True, help='new private directory outside the repository')
    p.add_argument('--events', help='ordered JSONL events supplied by a compatible reader, for any input format')
    args = p.parse_args()
    try:
        source = Path(args.input).expanduser().resolve()
        out = safe_output(Path(args.out))
        if not source.is_file(): raise ValueError('input is not a file')
        if source.suffix.lower() == '.hwpx' and not args.events:
            return subprocess.run([sys.executable, str(Path(__file__).with_name('extract_hwpx.py')),
                                   '--input', str(source), '--out', str(out)]).returncode
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        # Inspect the input before creating output, except extracted DOCX images.
        warnings = ['Extracted events are reading aids; check source layout, figures and citations in a suitable reader.',
                    'Structural anchors do not automatically establish printed page numbers.']
        images = []
        suffix = source.suffix.lower()
        if args.events:
            event_source = Path(args.events).expanduser().resolve()
            events = read_events(event_source)
            warnings.append('Events supplied by a separate reader require independent source verification.')
        elif suffix in ('.txt', '.md', '.markdown', '.rst', '.csv', '.tsv'):
            events = list(text_events(source))
        elif suffix in ('.html', '.htm'):
            parser = HTMLText(); parser.feed(source.read_text(encoding='utf-8-sig')); events = parser.events
            warnings.append('Scripts, styles, external resources and visual layout are not reproduced.')
        elif suffix == '.pdf':
            events = pdf_events(source)
            warnings.append('Physical page numbers are not necessarily printed page numbers; scanned pages and figures need OCR or visual review.')
        elif suffix == '.docx':
            events = None
            warnings.append('Headers, footers, floating shapes and complex tables can require visual verification.')
        else:
            raise ValueError('unsupported input format: use a suitable reader to create ordered events and pass --events')
        out.mkdir(parents=True); (out/'chunks').mkdir(); (out/'images').mkdir()
        if events is None: events, images = docx_events(source, out/'images')
        if not any(kind == 'TEXT' for kind, _, _ in events):
            warnings.append('No text events were extracted; use OCR or inspect the source visually.')
        chunks = []; lines = []; seq = 0; part = 1
        def flush():
            nonlocal part, lines
            if not lines: return
            filename = f'section0-{part:02}.txt'
            (out/'chunks'/filename).write_text('\n'.join(lines)+'\n', encoding='utf-8')
            preview = ' '.join(x.split('] TEXT ', 1)[1] for x in lines if '] TEXT ' in x)
            chunks.append({'file':'chunks/'+filename, 'section':'section0',
                           'first_anchor':lines[0].split(']')[0]+']', 'last_anchor':lines[-1].split(']')[0]+']',
                           'lines':len(lines), 'chars':sum(map(len,lines)), 'preview':preview[:250]})
            part += 1; lines = []
        for kind, path, text in events:
            seq += 1
            line = f'[section0:{seq:05d} {clean(path)}] {kind} {clean(text)}'.rstrip()
            lines.append(line)
            if sum(len(x)+1 for x in lines) >= 17000: flush()
        flush()
        manifest = {'source':str(source), 'sha256':digest, 'warnings':warnings, 'images':images, 'chunks':chunks}
        if args.events: manifest['events_sha256'] = hashlib.sha256(event_source.read_bytes()).hexdigest()
        (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (out/'INDEX.md').write_text('# Document reading chunks\n\nRead chunks in order; verify extracted content against the source.\n\n' +
            '\n'.join(f"- {c['file']}: {c['lines']} events; {c['preview']}" for c in chunks)+'\n',encoding='utf-8')
        print(json.dumps({'chunks':len(chunks),'events':len(events),'warnings':warnings},ensure_ascii=False))
        return 0
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, ET.ParseError, zipfile.BadZipFile) as e:
        print('error: '+str(e),file=sys.stderr); return 1

if __name__ == '__main__': sys.exit(main())
