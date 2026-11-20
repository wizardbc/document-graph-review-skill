import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS=Path(__file__).resolve().parents[1]/'skills/document-graph-review/scripts'
sys.path.insert(0,str(SCRIPTS))
import build_graph
SCRIPT=SCRIPTS/'extract_document.py'


def write_pdf(path):
    content=b'BT /F1 16 Tf 72 700 Td (Example document text) Tj ET'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',
             b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
             b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
             b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
             b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
    result=bytearray(b'%PDF-1.4\n'); offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(result)); result+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref=len(result); result+=f'xref\n0 {len(offsets)}\n'.encode()+b'0000000000 65535 f \n'
    for pos in offsets[1:]: result+=f'{pos:010d} 00000 n \n'.encode()
    result+=f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    path.write_bytes(result)


class DocumentFormatTests(unittest.TestCase):
    def run_extract(self,source,out,*more):
        p=subprocess.run([sys.executable,str(SCRIPT),'--input',str(source),'--out',str(out),*map(str,more)],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        return json.loads((out/'manifest.json').read_text())
    def test_text_flows_into_original_graph(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/'sample.txt'; out=root/'source'; source.write_text('Section one\nNorth team plans Blue service\n')
            m=self.run_extract(source,out)
            self.assertEqual(len(m['chunks']),1)
            agent=root/'kg/worker'; agent.mkdir(parents=True)
            claim={'id':'a','subject':'North team','subject_type':'Organization','predicate':'PLANS','object':'Blue service','object_type':'Service','source_chunk':'section0-01.txt','source_anchor':'section0:00002','quote':'North team plans Blue service'}
            (agent/'claims.jsonl').write_text(json.dumps(claim)+'\n')
            with contextlib.redirect_stdout(io.StringIO()): build_graph.build(out,root/'kg',root/'graph.sqlite')
            self.assertEqual(build_graph.stats(root/'graph.sqlite')['evidence_status'],{'verified':1})
    def test_docx_paragraph_table_image_and_footnote(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/'sample.docx'; out=root/'source'
            xml='''<w:document xmlns:w="urn:word" xmlns:a="urn:drawing" xmlns:r="urn:rel"><w:body><w:p><w:r><w:t>Example heading</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell value</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:r><a:blip r:embed="r1"/></w:r></w:p></w:body></w:document>'''
            rels='<Relationships><Relationship Id="r1" Target="media/image1.png"/></Relationships>'
            note='<w:footnotes xmlns:w="urn:word"><w:p><w:r><w:t>Note text</w:t></w:r></w:p></w:footnotes>'
            with zipfile.ZipFile(source,'w') as z:
                z.writestr('word/document.xml',xml); z.writestr('word/_rels/document.xml.rels',rels)
                z.writestr('word/footnotes.xml',note); z.writestr('word/media/image1.png',b'fake-image')
            m=self.run_extract(source,out); chunk=(out/m['chunks'][0]['file']).read_text()
            for val in ['TEXT Example heading','TABLE table','CELL row=0 col=0','TEXT Cell value','IMAGE ref=image1.png','TEXT Note text']:
                self.assertIn(val,chunk)
            self.assertEqual((out/'images/image1.png').read_bytes(),b'fake-image')
    def test_html_ignores_scripts_and_tracks_text(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/'page.html'; source.write_text('<h1>Heading</h1><script>hidden content</script><p>Visible content</p>')
            m=self.run_extract(source,root/'source'); chunk=(root/'source'/m['chunks'][0]['file']).read_text()
            self.assertIn('TEXT Heading',chunk);self.assertIn('TEXT Visible content',chunk);self.assertNotIn('hidden content',chunk)
    def test_any_format_can_use_reader_events(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/'document.bin'; source.write_bytes(b'fixture binary')
            events=root/'events.jsonl'; events.write_text(json.dumps({'kind':'TEXT','path':'page[1]/line[1]','text':'Reader extracted text'})+'\n')
            out=root/'source'; m=self.run_extract(source,out,'--events',events)
            self.assertIn('events_sha256',m)
            self.assertIn('TEXT Reader extracted text',(out/m['chunks'][0]['file']).read_text())
    @unittest.skipUnless(shutil.which('pdftotext'),'pdftotext is unavailable')
    def test_pdf_preserves_page_anchor(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/'sample.pdf'; write_pdf(source); out=root/'source'
            m=self.run_extract(source,out); chunk=(out/m['chunks'][0]['file']).read_text()
            self.assertIn('PAGE physical_page=1',chunk);self.assertIn('TEXT Example document text',chunk)

if __name__=='__main__': unittest.main()
