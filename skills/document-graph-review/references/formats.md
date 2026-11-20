# Formats and evidence

Use `extract_document.py` for the supported formats below. All produce the same anchored event and manifest structure consumed by the graph builder. They do not replace source inspection or guarantee every element was extracted.

- **HWPX:** Delegates to `extract_hwpx.py`, preserving the original review's section XML order, paragraph/run paths, tables and cell coordinates, embedded-image references and equations. XML anchors are structural references, not printed page numbers. Open the source in Hancom Office or another suitable reader to check layout and figure text.
- **PDF:** Uses the local `pdftotext` command to extract text with physical page and line anchors. Physical and printed page numbers can differ. Scanned pages, diagrams, and complex tables require OCR or visual review; page text alone is incomplete evidence.
- **DOCX:** Extracts document paragraphs, table cells, footnotes, endnotes, and embedded image references. Headers, footers, floating shapes, text boxes, and complex tables may require visual review in a suitable reader.
- **HTML:** Extracts visible text and structural tags without fetching external resources or executing scripts. Check visual layout and images separately.
- **Plain text, Markdown, CSV and TSV:** Extracts nonempty lines in source order. Row or line numbers do not imply printed page numbers.
- **Any other document:** Use a suitable available reader, converter or OCR to inspect the source and write ordered JSONL events, then pass `--events`. Preserve relevant structure, source locations and uncertainty. This route requires a reader capable of opening the actual format; it does not imply automatic support for every file.

For figures and tables, cite the page and specific caption, row/column or figure element. Missing, illegible or unsupported material remains an explicit gap. When asked for an output document, create an editable standalone file in the specified format, check that it opens and the text survives, and preserve the original.
