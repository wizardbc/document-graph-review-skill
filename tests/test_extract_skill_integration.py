import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/document-graph-review/scripts'))
import build_graph as graph

UPSTREAM = Path.home() / '.codex/skills/knowledge-graph-extract/scripts'

@unittest.skipUnless((UPSTREAM/'kg_mark.py').is_file(), 'install knowledge-graph-extract to exercise integration')
class ExtractionSkillIntegrationTest(unittest.TestCase):
    def test_progress_triples_and_anchored_claim_integration(self):
        def run(name,*args):
            p=subprocess.run([sys.executable,str(UPSTREAM/name),*map(str,args)],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr+'\n'+p.stdout)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'source'; chunks=source/'chunks'; agent=root/'kg/agent'; chunks.mkdir(parents=True); agent.mkdir(parents=True)
            chunk='section0-01.txt'
            (chunks/chunk).write_text('[section0:00001 p[1]/run[1]/t[1]] TEXT Cedar Lab plans Blue Service\n')
            (source/'manifest.json').write_text(json.dumps({'sha256':'fixture','source':'fixture','chunks':[{'file':'chunks/'+chunk,'section':'section0'}]}))
            manifest={'version':1,'docs_dir':str(chunks),'schema_file':'schema.json','documents':[{'path':chunk,'unit':'line','lines':1,'status':'pending','chunks':[{'id':1,'start_line':1,'end_line':1,'status':'pending','triplets':0}]}]}
            (agent/'manifest.json').write_text(json.dumps(manifest))
            (agent/'schema.json').write_text(json.dumps({'entity_types':['Organization','Service'],'relation_types':['PLANS']}))
            triple={'doc':chunk,'chunk':1,'subject':'Cedar Lab','subject_type':'Organization','relation':'PLANS','object':'Blue Service','object_type':'Service'}
            (agent/'triples.jsonl').write_text(json.dumps(triple)+'\n')
            (agent/'entities.jsonl').write_text('')
            claim={'id':'a1','subject':'Cedar Lab','subject_type':'Organization','predicate':'PLANS','object':'Blue Service','object_type':'Service','claim_type':'plan','source_chunk':chunk,'source_anchor':'section0:00001','quote':'Cedar Lab plans Blue Service'}
            (agent/'claims.jsonl').write_text(json.dumps(claim)+'\n')
            run('kg_mark.py',agent,chunk,'1','in_progress')
            run('validate_triples.py',agent)
            run('kg_mark.py',agent,chunk,'1','done','--triplets','1')
            with contextlib.redirect_stdout(io.StringIO()): graph.build(source,root/'kg',root/'graph.sqlite')
            stats=graph.stats(root/'graph.sqlite')
            self.assertEqual((stats['claims'],stats['chunks_declared_reviewed']),(1,1))
            self.assertEqual(stats['evidence_status'],{'verified':1})
            self.assertEqual(len(graph.search(root/'graph.sqlite','Blue Service')),1)

if __name__=='__main__': unittest.main()
