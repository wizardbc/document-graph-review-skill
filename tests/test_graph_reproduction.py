#!/usr/bin/env python3
"""Synthetic provenance and coverage regression test; never reads the real report."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/document-graph-review/scripts'))
import build_graph as g


def run_graph_test():
    with tempfile.TemporaryDirectory(prefix="kg-selftest-") as tmp:
        root = Path(tmp)
        source = root / "source"
        chunks = source / "chunks"
        claims = root / "claims"
        chunks.mkdir(parents=True)
        (source / "images").mkdir()
        (source / "images" / "image01.bmp").write_bytes(b"synthetic image placeholder")
        (claims / "agent_a").mkdir(parents=True)
        (claims / "agent_b").mkdir(parents=True)
        (source / "manifest.json").write_text(json.dumps({"sha256": "testhash", "source": "fixture",
            "chunks": [{"file": "chunks/section1-01.txt", "section": "section1"},
                       {"file": "chunks/section1-02.txt", "section": "section1"}]}))
        (chunks / "section1-01.txt").write_text(
            "[section1:00001 p[1]/run[1]/t[1]] TEXT Fixture target 12%\n"
            "[section1:00002 p[2]/run[1]/t[1]] TEXT effective year 2032\n"
            "[section1:00003 p[3]/run[1]/t[1]] TEXT Fixture target 31%\n"
            "[section1:00005 p[5]/run[1]/pic[1]] IMAGE id=1 refs=image01\n", encoding="utf-8")
        (chunks / "section1-02.txt").write_text("[section1:00004 p[4]] PARA\n", encoding="utf-8")
        base = {"id": "c1", "subject": "Fixture", "subject_type": "Product", "predicate": "TARGETS",
                "object": "12%", "object_type": "Target", "claim_type": "plan", "date": "2032",
                "conditions": "test", "source_chunk": "section1-01.txt",
                "source_anchor": "section1:00001; section1:00002", "quote": "Fixture target 12%; effective year 2032"}
        other = dict(base, id="c2", object="31%", source_anchor="section1:00003", quote="Fixture target 31%")
        bad = dict(base, id="bad", source_anchor="section1:00002", quote="Fixture target 12%")
        different_type = dict(base, id="typed", subject_type="Organization")
        image_claim = dict(base, id="img", source_anchor="section1:00005", quote="image text",
                           evidence_type="image", source_artifact="source/images/image01.bmp")
        check_file = root / "visual_checks.json"
        check_file.write_text(json.dumps([{"agent": "agent_a", "claim_id": "img", "source_anchor": "section1:00005",
            "source_artifact": "source/images/image01.bmp", "quote": "image text", "reviewers": ["reviewer"]}]))
        with (claims / "agent_a" / "claims.jsonl").open("w", encoding="utf-8") as f:
            for r in (base, other, bad, image_claim):
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.write("{malformed\n")
        (claims / "agent_b" / "claims.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in (different_type, dict(base, id="duplicate"))), encoding="utf-8")
        db = root / "test.sqlite"
        with contextlib.redirect_stdout(io.StringIO()):
            g.build(source, claims, db, visual_checks_file=check_file)
        result = g.stats(db)
        assert result["claims"] == 6 and result["parse_errors"] == 1, result
        assert result["evidence_status"] == {"review": 1, "verified": 4, "visual_confirmed": 1}, result
        assert result["chunks_without_claims"] == ["section1-02.txt"], result
        assert len(g.search(db, "Fixture")) == 5
        assert len(g.search(db, "Fixture", True)) == 6
        assert g.candidates(db)[0]["subject"] == "Fixture"
        result = g.neighbors(db, "Fixture", 1)
        assert len(result["edges"]) == 5 and not result["truncated"]
        ev = g.evidence(db, result["edges"][0]["record_id"])
        assert ev["anchored_events"] and ev["claim"]["evidence_status"] == "verified"
        # Same label but different explicit types must stay distinct.
        with g.db_conn(db) as con:
            assert con.execute("SELECT count(*) FROM nodes WHERE label='Fixture'").fetchone()[0] == 2
        print("synthetic provenance, duplicates, coverage, numeric candidates: PASS")


class ActualGraphMethodTest(unittest.TestCase):
    def test_synthetic_graph(self):
        run_graph_test()

if __name__ == "__main__":
    unittest.main()
