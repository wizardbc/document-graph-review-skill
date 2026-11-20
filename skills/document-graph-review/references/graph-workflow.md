# Graph workflow used in the review

## Dependencies and private workspace

The actual section extraction used [`knowledge-graph-extract`](https://github.com/maddataanalyst/knowledge-graph-extract/tree/542fffaeaf18f4db6eb3f32c7a93c2c54822f67c/skills/knowledge-graph-extract), pinned at commit `542fffaeaf18f4db6eb3f32c7a93c2c54822f67c`. If missing, use the skill installer to install that repository path and revision, then read its `SKILL.md`. The scripts below use only Python's standard library. No source text is sent to the dependency's repository.

If the extractor is missing, install it using the Codex skill-installer helper (the revision and repository are public; do not send document data):

```sh
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-installer/scripts/install-skill-from-github.py" --repo maddataanalyst/knowledge-graph-extract --path skills/knowledge-graph-extract --ref 542fffaeaf18f4db6eb3f32c7a93c2c54822f67c
```

Make a private workspace, e.g. `/private/work/review`, outside this repository. Use the current skill's actual absolute `scripts` path, abbreviated as `$SCRIPTS` below. `$WORK` is the private review directory. Set these variables to local paths; do not put source content or identifiers into public examples.

```sh
python3 "$SCRIPTS/extract_document.py" --input "$WORK/document.pdf" --out "$WORK/source"
```

The same command accepts HWPX, DOCX, HTML and plain-text files. HWPX invokes the original review's extractor unchanged; PDF uses the local `pdftotext` command. For any other format, have a suitable reader produce an ordered, private JSONL file with `kind`, `path` and `text` fields and use `--events "$WORK/events.jsonl"`. Example event (fictional):

```json
{"kind":"TEXT","path":"page[1]/paragraph[2]","text":"Example source text"}
```

Use `TEXT`, `PARA`, `TABLE`, `CELL`, `IMAGE` or other uppercase event kinds as applicable and preserve source page, table/cell and figure references in `path` and `text`. The event file must be made from the source by a compatible reader; the helper does not open unsupported formats itself. Its hash is recorded alongside the original file hash. `--out` must not exist. Where available, images are copied to `source/images`. For a visually reviewed image supplied through events, store it there and include its filename in the `IMAGE` event; see the visual evidence format below.

The extractor emits ordered `source/chunks/*.txt`, `source/manifest.json`, and `source/INDEX.md`. The manifest includes source hash, boundaries and extraction warnings. Do not read the entire document into an agent context; use the index to assign bounded consecutive chunks and read each assigned chunk in order. Anchors preserve source references but do not establish printed page numbers or complete figure text. Check important passages, scanned pages and figures visually in a suitable reader.

## Per-agent review

Give each permitted worker a disjoint ordered chunk list, a private agent directory under `$WORK/kg/`, the `knowledge-graph-extract` skill, and the claim format below. The parent or coordinator manages the shared section plan; workers keep separate files. Use the extraction skill's manifest, registry and progress commands; a claim record does not prove a whole chunk was read. Preserve source records before running validators. Its duplicate detector ignores provenance, so do not apply automatic deduplication to the only evidence copy. Track empty, unassigned and unreadable chunks separately.

Each worker writes `$WORK/kg/<agent>/claims.jsonl` in addition to extraction-skill outputs. One line per source-backed assertion; repeated claims from different evidence remain separate. Example below is entirely fictional:

```json
{"id":"c1","subject":"Example Lab","subject_type":"Organization","predicate":"PLANS","object":"Example Service","object_type":"Service","claim_type":"plan","conditions":"first release","date":"2032","source_chunk":"section0-01.txt","source_anchor":"section0:00002","quote":"Example Lab plans Example Service"}
```

Use one or more exact event anchors within the cited chunk, separated by semicolons, and exact quote fragments from those events. The match is location validation, not proof of truth. Record statement status, time, units, conditions and population wherever relevant. `source_anchor` refers to extractor event IDs, not PDF page numbers. `manifest.json` is the extraction skill's per-agent manifest, whose document paths are chunk filenames. Visual evidence uses a claim with `evidence_type: "image"` and `source_artifact: "source/images/<filename>"`; collect an independent private `visual_checks.json` array with `agent`, `claim_id`, `source_anchor`, `source_artifact`, `quote`, and nonempty `reviewers`. It records an attestation, not automatic verification. Keep unresolved or illegible items explicit.

## Build, inspect and verify

After workers finish writing, build from their records and manifests. Use the same source, agent outputs, external files and visual checks when rebuilding; the graph builder replaces the specified graph database atomically. External findings are separate JSONL records with `subject`, `relation`, `object`, `source`, `perspective` and `evidence`; the builder preserves them but does not independently validate their links or content. Supply `--external-file` once per file and `--visual-checks` when these exist.

```sh
python3 "$SCRIPTS/build_graph.py" build --source-root "$WORK/source" --claims-root "$WORK/kg" --db "$WORK/graph.sqlite"
python3 "$SCRIPTS/build_graph.py" stats --db "$WORK/graph.sqlite"
python3 "$SCRIPTS/build_graph.py" search --db "$WORK/graph.sqlite" --include-review 'Example Service'
python3 "$SCRIPTS/build_graph.py" neighbors --db "$WORK/graph.sqlite" --include-review --depth 2 'Example Service'
python3 "$SCRIPTS/build_graph.py" candidates --db "$WORK/graph.sqlite" --include-review
python3 "$SCRIPTS/build_graph.py" evidence --db "$WORK/graph.sqlite" 'agent:line:claim-id'
python3 "$SCRIPTS/build_graph.py" search-external --db "$WORK/graph.sqlite" 'Example Service'
```

Default search excludes unresolved records; `--include-review` brings them back. `verified` means matching quote fragments were found at the specified anchors only. `visual_confirmed` means the metadata and independent visual record match, not that the code read an image. `review` and `unlocated` retain unresolved records. `stats` distinguishes declared completed coverage from chunks with claims; neither proves comprehension. `neighbors` uses exact labels, caps traversal at two hops and 50 labels, and reports truncation; labels with different types are not necessarily the same entity. `candidates` groups differing numbers for the same subject and predicate; first verify period, units, condition, scope and plan/actual status before judging.

Search independently by aliases, dates, numbers, concepts, tables and summaries; no single query is exhaustive. Reopen cited source locations, inspect important figures and map printed pages using the original application when available. Check external claims and document author statements against primary sources separately. Review independent counterevidence and report errors separately from assumptions and unresolved questions. Preserve the complete private graph and records for audit.

## Reproducibility

The HWPX extractor and graph builder are adapted from the actual review scripts; source/output paths and a general numeric-unit matcher were generalized. The multi-format extractor is an additional adapter into the same graph input format. With identical inputs, extraction and graph records can be reproduced deterministically. Agents still perform semantic extraction and evaluation: another independent run can produce different claims or conclusions. Reproduce a previous graph using its preserved source, per-agent files, external records, and visual checks; never claim exact regeneration if those inputs are missing. Test this repository with `python3 -m unittest discover -s tests -v` using synthetic fixtures.
