#!/usr/bin/env python3
"""Build an auditable, evidence-preserving SQLite graph from per-agent claim files.

This tool indexes extracted source events, not the original document, and does not decide whether
claims are true. Ambiguous or unverified claims remain queryable as review items.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import unicodedata

ANCHOR = re.compile(r"section\d+:\d+")
LINE_ANCHOR = re.compile(r"^\[(section\d+:\d+)\s")
NUMBERS = re.compile(r"(?<![\w])[-+]?\d[\d,]*(?:\.\d+)?\s*(?:%|[^\W\d_]+)?", re.I)


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def normalized(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", as_text(value))).strip()


def fragments(value):
    # Agent records may quote independent cells or omit text using an ellipsis.
    # Each remaining fragment must still appear in one of the cited events.
    return [normalized(x) for x in re.split(r"[;；]|\.{3,}|…", as_text(value)) if normalized(x)]


def load_events(path):
    events = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = LINE_ANCHOR.match(line)
        if match:
            # Most evidence is a TEXT event. Keep other lines for traceability.
            text = line.split(" TEXT ", 1)[1] if " TEXT " in line else ""
            events[match.group(1)] = (lineno, line, text)
    return events


def validate_evidence(record, event_map, chunk_exists):
    anchor_string = as_text(record.get("source_anchor"))
    anchors = list(dict.fromkeys(ANCHOR.findall(anchor_string)))
    absent = [a for a in anchors if a not in event_map]
    present = [a for a in anchors if a in event_map]
    qs = fragments(record.get("quote"))
    # Only cited anchor text is used for verification, not arbitrary chunk text.
    texts = [normalized(event_map[a][2]) for a in present if event_map[a][2]]
    joined = " ".join(texts)
    outcomes = [any(q in t for t in texts) or q in joined for q in qs]
    reasons = []
    if not chunk_exists:
        reasons.append("missing_chunk")
    if not anchors:
        reasons.append("missing_anchor")
    if absent:
        reasons.append("anchor_not_in_chunk:" + ",".join(absent))
    if not qs:
        reasons.append("missing_quote")
    if qs and not all(outcomes):
        reasons.append("quote_fragment_not_found_at_cited_anchor")
    if not reasons:
        state = "verified"
    elif not chunk_exists or not present:
        state = "unlocated"
    else:
        state = "review"
    return state, reasons, anchors, outcomes


DDL = """
PRAGMA foreign_keys = ON;
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE chunks(filename TEXT PRIMARY KEY, section TEXT, first_anchor TEXT,
                    last_anchor TEXT, source_sha256 TEXT, event_count INTEGER NOT NULL);
CREATE TABLE source_events(chunk TEXT NOT NULL, anchor TEXT NOT NULL, line_number INTEGER NOT NULL,
                           raw_line TEXT NOT NULL, text TEXT NOT NULL,
                           PRIMARY KEY(chunk, anchor), FOREIGN KEY(chunk) REFERENCES chunks(filename));
CREATE TABLE review_coverage(chunk TEXT NOT NULL, agent TEXT NOT NULL, status TEXT NOT NULL,
                             declared_lines INTEGER, declared_triplets INTEGER, source_file TEXT NOT NULL,
                             PRIMARY KEY(chunk,agent), FOREIGN KEY(chunk) REFERENCES chunks(filename));
CREATE TABLE nodes(node_id TEXT PRIMARY KEY, label TEXT NOT NULL, node_type TEXT NOT NULL,
                   UNIQUE(label, node_type));
CREATE TABLE claims(record_id TEXT PRIMARY KEY, agent TEXT NOT NULL, source_file TEXT NOT NULL,
                    source_line INTEGER NOT NULL, original_id TEXT NOT NULL,
                    subject TEXT NOT NULL, subject_type TEXT NOT NULL, subject_node TEXT,
                    predicate TEXT NOT NULL, object TEXT NOT NULL, object_type TEXT NOT NULL,
                    object_node TEXT, claim_type TEXT, conditions TEXT, claim_date TEXT,
                    source_chunk TEXT, source_anchor TEXT, quote TEXT,
                    evidence_status TEXT NOT NULL, evidence_reasons TEXT NOT NULL,
                    raw_json TEXT NOT NULL, FOREIGN KEY(subject_node) REFERENCES nodes(node_id),
                    FOREIGN KEY(object_node) REFERENCES nodes(node_id));
CREATE TABLE claim_anchors(record_id TEXT NOT NULL, anchor TEXT NOT NULL,
                           PRIMARY KEY(record_id, anchor), FOREIGN KEY(record_id) REFERENCES claims(record_id));
CREATE TABLE visual_checks(record_id TEXT PRIMARY KEY, review_json TEXT NOT NULL,
                           FOREIGN KEY(record_id) REFERENCES claims(record_id));
CREATE TABLE input_errors(source_file TEXT NOT NULL, source_line INTEGER NOT NULL,
                          reason TEXT NOT NULL, raw_line TEXT NOT NULL);
CREATE TABLE external_claims(record_id TEXT PRIMARY KEY, source_file TEXT NOT NULL, source_line INTEGER NOT NULL,
                             subject TEXT NOT NULL, relation TEXT NOT NULL, object TEXT NOT NULL,
                             source TEXT NOT NULL, perspective TEXT NOT NULL, evidence TEXT NOT NULL,
                             status TEXT NOT NULL, raw_json TEXT NOT NULL);
CREATE INDEX claims_subject_predicate ON claims(subject,predicate);
CREATE INDEX claims_source_chunk ON claims(source_chunk);
CREATE INDEX claims_evidence_status ON claims(evidence_status);
CREATE INDEX claims_object ON claims(object);
CREATE INDEX claims_agent ON claims(agent);
"""


def node_id(label, node_type):
    return hashlib.sha256((node_type + "\0" + label).encode()).hexdigest()[:24]


def add_node(conn, label, node_type):
    if not label:
        return None
    key = node_id(label, node_type)
    conn.execute("INSERT OR IGNORE INTO nodes VALUES (?, ?, ?)", (key, label, node_type))
    return key


def build(source_root, claims_root, db, external_files=(), visual_checks_file=None):
    source_root, claims_root, db = map(Path, (source_root, claims_root, db))
    skill_dir = Path(__file__).resolve().parents[1]
    check = subprocess.run(["git", "-C", str(skill_dir), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    protected = Path(check.stdout.strip()).resolve() if check.returncode == 0 else skill_dir
    target = db.expanduser().resolve()
    if target == protected or protected in target.parents:
        raise ValueError("Graph output must be outside the skill repository")
    manifest = json.loads((source_root / "manifest.json").read_text(encoding="utf-8"))
    checks = {}
    if visual_checks_file:
        records = json.loads(Path(visual_checks_file).read_text(encoding="utf-8"))
        checks = {(as_text(r.get("agent")), as_text(r.get("claim_id"))): r for r in records}
    chunks = manifest["chunks"]
    source_hash = as_text(manifest.get("sha256"))
    db.parent.mkdir(parents=True, exist_ok=True)
    fd, tempname = tempfile.mkstemp(prefix=".graph-", suffix=".sqlite", dir=db.parent)
    os.close(fd)
    con = sqlite3.connect(tempname)
    try:
        con.executescript(DDL)
        con.execute("INSERT INTO meta VALUES (?,?)", ("source_sha256", source_hash))
        con.execute("INSERT INTO meta VALUES (?,?)", ("source", as_text(manifest.get("source"))))
        event_cache = {}
        for chunk in chunks:
            name = Path(chunk["file"]).name
            file = source_root / "chunks" / name
            events = load_events(file) if file.is_file() else {}
            event_cache[name] = events
            con.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?)", (
                name, as_text(chunk.get("section")), as_text(chunk.get("first_anchor")),
                as_text(chunk.get("last_anchor")), source_hash, len(events)))
            con.executemany("INSERT INTO source_events VALUES (?,?,?,?,?)", [
                (name, anchor, line, raw, text) for anchor, (line, raw, text) in events.items()])
        for filename in sorted(claims_root.glob("*/manifest.json")):
            manifest_agent = json.loads(filename.read_text(encoding="utf-8"))
            for doc in manifest_agent.get("documents", []):
                name = Path(as_text(doc.get("path"))).name
                if name not in event_cache:
                    continue
                chunks_declared = doc.get("chunks", [])
                done = doc.get("status") == "done" and all(c.get("status") == "done" for c in chunks_declared)
                status = "declared_done" if done else "not_done"
                con.execute("INSERT OR REPLACE INTO review_coverage VALUES (?,?,?,?,?,?)", (
                    name, filename.parent.name, status, doc.get("lines"),
                    sum(c.get("triplets", 0) or 0 for c in chunks_declared), str(filename)))
        for filename in sorted(claims_root.glob("*/claims.jsonl")):
            agent = filename.parent.name
            with filename.open(encoding="utf-8") as f:
                for line_number, raw in enumerate(f, 1):
                    if not raw.strip():
                        continue
                    try:
                        rec = json.loads(raw)
                        if not isinstance(rec, dict):
                            raise ValueError("claim is not a JSON object")
                    except (ValueError, json.JSONDecodeError) as exc:
                        con.execute("INSERT INTO input_errors VALUES (?,?,?,?)", (str(filename), line_number, str(exc), raw.rstrip()))
                        continue
                    subject = as_text(rec.get("subject")).strip()
                    obj = as_text(rec.get("object")).strip()
                    pred = as_text(rec.get("predicate")).strip()
                    stype = as_text(rec.get("subject_type")).strip() or "unspecified"
                    otype = as_text(rec.get("object_type")).strip() or "unspecified"
                    name = Path(as_text(rec.get("source_chunk"))).name
                    events = event_cache.get(name, {})
                    status, reasons, anchors, _ = validate_evidence(rec, events, name in event_cache)
                    visual = checks.get((agent, as_text(rec.get("id"))))
                    if visual:
                        va = as_text(visual.get("source_anchor"))
                        artifact = as_text(visual.get("source_artifact"))
                        artifact_path = source_root.parent / artifact
                        line = events.get(va, (0,"",""))[1]
                        expected_dir = (source_root / "images").resolve()
                        matching_visual = (
                            as_text(rec.get("evidence_type")) == "image" and va in anchors
                            and as_text(rec.get("source_artifact")) == artifact
                            and artifact_path.resolve().parent == expected_dir and artifact_path.is_file()
                            and " IMAGE " in line and artifact_path.stem.lower() in line.lower()
                            and normalized(rec.get("quote")) == normalized(visual.get("quote"))
                            and bool(visual.get("reviewers")))
                        if matching_visual:
                            status = "visual_confirmed"
                            reasons = ["visual_review_attested_not_machine_text_verified"]
                        else:
                            visual = None
                            reasons.append("visual_review_metadata_mismatch")
                    if not (subject and obj and pred):
                        status = "review" if status == "verified" else status
                        reasons.append("missing_graph_field")
                    rid = f"{agent}:{line_number}:{as_text(rec.get('id'))}"
                    s_node = add_node(con, subject, stype)
                    o_node = add_node(con, obj, otype)
                    con.execute("""INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        rid, agent, str(filename), line_number, as_text(rec.get("id")), subject, stype,
                        s_node, pred, obj, otype, o_node, as_text(rec.get("claim_type")),
                        as_text(rec.get("conditions")), as_text(rec.get("date")), name,
                        as_text(rec.get("source_anchor")), as_text(rec.get("quote")), status,
                        json.dumps(reasons, ensure_ascii=False), json.dumps(rec, ensure_ascii=False, sort_keys=True)))
                    con.executemany("INSERT INTO claim_anchors VALUES (?,?)", [(rid, a) for a in anchors])
                    if visual is not None:
                        con.execute("INSERT INTO visual_checks VALUES (?,?)", (rid,json.dumps(visual,ensure_ascii=False,sort_keys=True)))
        # Preserve external researchers' distinct record format separately. This is
        # a link index, not a claim that web pages or quoted content were verified.
        for filename in map(Path, external_files):
            if not filename.is_file():
                raise FileNotFoundError(f"external claim file missing: {filename}")
            with filename.open(encoding="utf-8") as f:
                for line_number, raw in enumerate(f, 1):
                    if not raw.strip():
                        continue
                    try:
                        rec = json.loads(raw)
                        if not isinstance(rec, dict):
                            raise ValueError("external claim is not a JSON object")
                    except (ValueError, json.JSONDecodeError) as exc:
                        con.execute("INSERT INTO input_errors VALUES (?,?,?,?)", (str(filename),line_number,str(exc),raw.rstrip()))
                        continue
                    rid = f"{filename.parent.name}:{line_number}:{as_text(rec.get('id'))}"
                    con.execute("INSERT INTO external_claims VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                        rid,str(filename),line_number,as_text(rec.get("subject")),as_text(rec.get("relation")),
                        as_text(rec.get("object")),as_text(rec.get("source")),as_text(rec.get("perspective")),
                        as_text(rec.get("evidence")),"not_independently_verified_by_graph",json.dumps(rec,ensure_ascii=False,sort_keys=True)))
        con.commit()
        con.execute("PRAGMA integrity_check").fetchone()
        con.close()
        os.replace(tempname, db)
    except BaseException:
        con.close()
        Path(tempname).unlink(missing_ok=True)
        raise
    print(json.dumps(stats(db), ensure_ascii=False, indent=2))


def db_conn(db):
    conn = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def stats(db):
    with db_conn(db) as con:
        counts = dict(con.execute("SELECT evidence_status, count(*) FROM claims GROUP BY evidence_status"))
        agents = {r[0]: {"claims": r[1], "text_anchor_matched": r[2], "manual_visual_confirmed": r[3]}
                  for r in con.execute("""SELECT agent,count(*),sum(evidence_status='verified'),
                     sum(evidence_status='visual_confirmed') FROM claims GROUP BY agent ORDER BY agent""")}
        chunks = [dict(r) for r in con.execute("""SELECT c.filename, c.section, count(cl.record_id) AS claims,
            sum(CASE WHEN cl.evidence_status='verified' THEN 1 ELSE 0 END) AS verified
            FROM chunks c LEFT JOIN claims cl ON c.filename=cl.source_chunk GROUP BY c.filename ORDER BY c.filename""")]
        done_chunks = {r[0] for r in con.execute("SELECT DISTINCT chunk FROM review_coverage WHERE status='declared_done'")}
        return {"source_sha256": con.execute("SELECT value FROM meta WHERE key='source_sha256'").fetchone()[0],
                "nodes": con.execute("SELECT count(*) FROM nodes").fetchone()[0],
                "claims": sum(counts.values()), "evidence_status": counts,
                "parse_errors": con.execute("SELECT count(*) FROM input_errors").fetchone()[0],
                "external_claims_separately_preserved": con.execute("SELECT count(*) FROM external_claims").fetchone()[0],
                "agents": agents, "chunks_with_claims": sum(1 for c in chunks if c["claims"]),
                "chunks_total": len(chunks), "chunks_without_claims": [c["filename"] for c in chunks if not c["claims"]],
                "chunks_declared_reviewed": len(done_chunks),
                "chunks_without_completed_manifest": [c["filename"] for c in chunks if c["filename"] not in done_chunks],
                "coverage_note": "A claim in a chunk does not establish that the whole chunk was read; zero claims does not establish it was unread.",
                "chunks": chunks}


def search(db, term, include_review=False):
    with db_conn(db) as con:
        status = "" if include_review else "AND evidence_status IN ('verified','visual_confirmed')"
        sql = f"""SELECT record_id,subject,predicate,object,claim_type,conditions,claim_date,
                  source_chunk,source_anchor,quote,evidence_status,evidence_reasons FROM claims
                  WHERE (subject LIKE ? OR object LIKE ? OR quote LIKE ? OR conditions LIKE ?) {status}
                  ORDER BY source_chunk,source_line"""
        args = [f"%{term}%"] * 4
        return [dict(r) for r in con.execute(sql, args)]


def search_external(db, term):
    with db_conn(db) as con:
        sql = """SELECT * FROM external_claims WHERE subject LIKE ? OR object LIKE ? OR source LIKE ?
                 OR perspective LIKE ? OR evidence LIKE ? ORDER BY source_file,source_line"""
        return [dict(r) for r in con.execute(sql, [f"%{term}%"] * 5)]


def candidates(db, include_review=False):
    with db_conn(db) as con:
        where = "" if include_review else "WHERE evidence_status IN ('verified','visual_confirmed')"
        rows = [dict(r) for r in con.execute(f"""SELECT record_id,subject,predicate,object,conditions,claim_date,
            source_chunk,source_anchor,quote,evidence_status FROM claims {where} ORDER BY subject,predicate,source_chunk""")]
    groups = {}
    for row in rows:
        numbers = tuple(normalized(n) for n in NUMBERS.findall(row["object"]))
        if numbers:
            groups.setdefault((row["subject"], row["predicate"]), []).append((numbers, row))
    out = []
    for (subject, predicate), group in groups.items():
        if len({item[0] for item in group}) > 1:
            out.append({"subject": subject, "predicate": predicate,
                        "warning": "Numeric candidate only; compare metric, unit, conditions, date and whether fact or target before judging a conflict.",
                        "claims": [item[1] for item in group]})
    return out


def neighbors(db, label, depth=1, include_review=False):
    """Inspect up to two hops; matching labels are not claimed to be same entities."""
    if depth < 1 or depth > 2:
        raise ValueError("depth must be 1 or 2")
    status = "" if include_review else "AND evidence_status IN ('verified','visual_confirmed')"
    seen_labels, frontier, edges = {label}, {label}, {}
    truncated = False
    with db_conn(db) as con:
        for _ in range(depth):
            next_frontier = set()
            for name in sorted(frontier):
                rows = con.execute(f"""SELECT record_id,subject,subject_type,predicate,object,object_type,
                    conditions,claim_date,source_chunk,source_anchor,quote,evidence_status
                    FROM claims WHERE (subject=? OR object=?) {status} ORDER BY record_id""", (name, name))
                for raw in rows:
                    row = dict(raw)
                    new = {row["subject"], row["object"]} - seen_labels
                    if len(seen_labels) + len(new) > 50:
                        truncated = True
                        continue
                    seen_labels.update(new)
                    next_frontier.update(new)
                    edges[row["record_id"]] = row
            frontier = next_frontier
    return {"start_label": label, "depth": depth, "truncated": truncated, "labels_returned": len(seen_labels),
            "warning": "Matching is by exact text label, not a confirmed identity; inspect types, conditions and provenance.",
            "edges": list(edges.values())}


def evidence(db, record_id):
    with db_conn(db) as con:
        claim = con.execute("SELECT * FROM claims WHERE record_id=?", (record_id,)).fetchone()
        if claim is None:
            raise ValueError("record ID not found")
        evidence = con.execute("""SELECT a.anchor,e.line_number,e.raw_line,e.text FROM claim_anchors a
            LEFT JOIN source_events e ON e.anchor=a.anchor AND e.chunk=?
            WHERE a.record_id=? ORDER BY e.line_number""", (claim["source_chunk"], record_id))
        return {"claim": dict(claim), "anchored_events": [dict(r) for r in evidence]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="Build the graph only after agent files have been finalized")
    b.add_argument("--source-root", required=True)
    b.add_argument("--claims-root", required=True)
    b.add_argument("--db", required=True)
    b.add_argument("--external-file", action="append", default=[])
    b.add_argument("--visual-checks")
    for name in ("stats", "search", "search-external", "candidates", "neighbors", "evidence"):
        q = sub.add_parser(name)
        q.add_argument("--db", required=True)
        if name in ("search", "candidates", "neighbors"):
            q.add_argument("--include-review", action="store_true")
        if name in ("search", "search-external"):
            q.add_argument("term")
        if name == "neighbors":
            q.add_argument("label")
            q.add_argument("--depth", type=int, choices=[1, 2], default=1)
        if name == "evidence":
            q.add_argument("record_id")
    args = p.parse_args()
    if args.cmd == "build":
        build(args.source_root, args.claims_root, args.db, args.external_file, args.visual_checks)
    elif args.cmd == "stats":
        print(json.dumps(stats(args.db), ensure_ascii=False, indent=2))
    elif args.cmd == "search":
        print(json.dumps(search(args.db, args.term, args.include_review), ensure_ascii=False, indent=2))
    elif args.cmd == "search-external":
        print(json.dumps(search_external(args.db, args.term), ensure_ascii=False, indent=2))
    elif args.cmd == "candidates":
        print(json.dumps(candidates(args.db, args.include_review), ensure_ascii=False, indent=2))
    elif args.cmd == "neighbors":
        print(json.dumps(neighbors(args.db, args.label, args.depth, args.include_review), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(evidence(args.db, args.record_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
