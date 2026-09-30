# 0005: Fallback parser chain (docling → docling+OCR → pdfplumber)

## Status

Accepted (refines ADR 0001 and the PDF-parsing research note)

## Context

Getting the text of *every* Paper is pivotal: a PDF that parses to nothing never
reaches the artifact, and in a medical corpus a silently-dropped Paper is a
silently-missed gap. The funnel diagnostic from issue #46 (Step 1) showed two
distinct parse failures on the real corpora:

- **Image-only / scanned PDFs** — docling's default (text-layer) pipeline
  returns empty or near-empty Markdown because there is no text to extract. On
  `takotsubo-50`, 0/50 PDFs parsed.
- **PDFs docling chokes on entirely** — a raised exception discarded the Paper.

ADR 0001 chose docling as the primary parser behind a `PdfParser` seam and
explicitly anticipated swapping in GROBID or marker. The seam existed, but only
a single parser sat behind it, so there was no recovery path when docling
failed.

## Decision

Introduce a **`FallbackParser`** that composes an ordered list of `PdfParser`s
behind the existing seam. For each PDF it returns the first parser's output that
is *usable* (its full text meets `MIN_USABLE_CHARS`); a parser that raises or
returns near-empty text is skipped and the next is tried. When every parser
fails it raises `ParserChainError`, which `parse_corpus` records as a per-Paper
failure without aborting the run (issue #46 Step 3, "surface, don't block").

The shipped chain (`default_parser`) is:

1. **docling** — the primary, section-aware parser (ADR 0001), unchanged.
2. **docling + OCR** — the same library with its OCR pipeline enabled, which is
   what lets it read image-only PDFs. This directly targets the likely root
   cause of empty parses.
3. **pdfplumber** — a light, permissive (MIT), pure-Python last resort for when
   docling itself crashes. It recovers no section structure (all text lands in
   one `other` section), but a Paper with a readable text layer can still be
   extracted.

Each `ParsedPaper` records which parser produced it, so a rescue is visible and
reproducible. The chain owns the concrete library types; the pipeline still only
ever sees `ParsedPaper`, and the suite tests the chain offline with fake
parsers.

GROBID and marker are **not** shipped: GROBID needs a running Java service and
marker bundles model weights with a non-commercial rider (PDF-parsing research
note), both at odds with an easy-to-install FOSS tool. The chain is a plain list
of `PdfParser`s, so either can be appended later without touching the pipeline.

## Consequences

- Scanned PDFs that returned nothing now get an OCR pass before being declared a
  failure; docling crashes fall back to pdfplumber instead of dropping the
  Paper. Fewer Papers fall out of the funnel at the parse stage.
- OCR is markedly slower than the text pipeline, but it only runs on PDFs the
  faster stage could not read, so the common case is unaffected.
- pdfplumber output is unsectioned, so an extraction built solely on it sees one
  `other` section. That is acceptable (the extract stage reads full text), and
  the funnel diagnostic still flags a collapsed/near-empty result.
- A PDF that every parser fails (e.g. a scan with no OCR-recoverable text) is
  still surfaced, now with the reason from each attempt — the input for the
  dashboard's "needs manual attention" queue (issue #46 Step 3).
- The `pdf` optional extra gains `pdfplumber`; docling's OCR needs no new
  dependency. Neither the OCR nor the pdfplumber adapter is exercised by the
  offline suite (same posture as the existing docling adapter); they are thin,
  lazy-imported library calls behind injectable seams.
