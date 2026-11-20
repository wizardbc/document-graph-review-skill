import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/document-graph-review/scripts/extract_hwpx.py'

class HwpxExtractionTest(unittest.TestCase):
    def test_table_image_and_stable_anchors(self):
        xml = '''<sec xmlns="urn:synthetic"><p><run><t>Example heading</t></run></p>
        <p><run><tbl id="1" rowCnt="1" colCnt="1"><tr><tc><cellAddr rowAddr="0" colAddr="0"/><cellSpan rowSpan="1" colSpan="1"/><p><run><t>Fixture value 12%</t></run></p></tc></tr></tbl></run></p>
        <p><run><pic id="4"><img binaryItemIDRef="image01"/></pic></run></p></sec>'''
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'fixture.hwpx'
            with zipfile.ZipFile(source,'w') as z:
                z.writestr('Contents/section0.xml',xml)
                z.writestr('BinData/image01.png',b'fixture-image')
            manifests=[]
            for label in ('one','two'):
                out=root/label
                p=subprocess.run([sys.executable,str(SCRIPT),'--input',str(source),'--out',str(out)],capture_output=True,text=True)
                self.assertEqual(p.returncode,0,p.stderr)
                m=json.loads((out/'manifest.json').read_text()); manifests.append(m)
                self.assertEqual(m['sha256'],hashlib.sha256(source.read_bytes()).hexdigest())
                chunk=(out/m['chunks'][0]['file']).read_text()
                self.assertIn('TEXT Example heading',chunk)
                self.assertIn('CELL row=0 col=0 rowspan=1 colspan=1',chunk)
                self.assertIn('TEXT Fixture value 12%',chunk)
                self.assertIn('IMAGE id=4 refs=image01',chunk)
                self.assertEqual((out/'images/image01.png').read_bytes(),b'fixture-image')
            self.assertEqual(manifests[0],manifests[1])
            self.assertEqual((root/'one/chunks/section0-01.txt').read_bytes(),(root/'two/chunks/section0-01.txt').read_bytes())

if __name__ == '__main__': unittest.main()
