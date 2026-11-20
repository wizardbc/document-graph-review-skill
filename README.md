# Document Graph Review

A Codex skill for reviewing long documents section by section and finding factual errors and contradictions with an evidence-linked knowledge graph. It packages the workflow and tools used in a document review.

1. Split a document into structurally anchored chunks, preserving paragraphs, tables, page references, and images where the format permits.
2. Have agents use `knowledge-graph-extract` to read assigned sections in order and record claims and evidence.
3. Combine claims, sources, progress records, and external findings in an SQLite graph.
4. Search across sections, follow relationships, and investigate numerical differences against the original text, calculations, and external sources.
5. Provide locations, explanations, and replacement text that can be copied into the document.

## Installation and use

Ask Codex's `$skill-installer` to install `skills/document-graph-review` from this repository. The required `knowledge-graph-extract` dependency and pinned version are documented in the [graph workflow](skills/document-graph-review/references/graph-workflow.md). In a subsequent turn, invoke `document-graph-review` and provide the document and desired output. The included extractor handles HWPX, PDF, DOCX, HTML, and plain-text formats; other formats can be read with a compatible tool and converted to source-anchored events. PDF extraction requires `pdftotext`. Scans and complex figures require OCR or visual review. Parallel agents are used when authorized and available.

Keep original documents, extracted text, graphs, and review results in a **private working directory outside this public repository**. This repository contains no actual user documents or review results.

Given the same document, agent records, visual checks, and external findings, the extraction results and graph can be rebuilt. A new independent review is not guaranteed to produce identical claims or conclusions.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The public tests use fictional data only.
