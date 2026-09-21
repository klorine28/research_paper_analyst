# Research: PDF parsing library for section-aware extraction

Question from the grilling session (ADR 0001 left this open): which
open-source library parses scientific-paper PDFs into sections for the
Extraction stage? The FOSS constraint (ADR 0002) makes **license** a primary
criterion.

Sources: GitHub repository metadata (license, activity) fetched via the
GitHub API, 2025.

## Candidates

| Library | License | Stars | What it does |
| --- | --- | --- | --- |
| [docling](https://github.com/docling-project/docling) | MIT | ~67.5K | Layout-aware document → structured Markdown/JSON; built for gen-AI pipelines (IBM/LF AI) |
| [marker](https://github.com/datalab-to/marker) | Apache-2.0¹ | ~39.9K | High-accuracy PDF → Markdown/JSON, ML-based |
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) | AGPL-3.0 | ~10.8K | Fast low-level text/layout extraction |
| [pdfplumber](https://github.com/jsvine/pdfplumber) | MIT | ~10.8K | Char/line/table-level extraction, pure Python |
| [pypdf](https://github.com/py-pdf/pypdf) | BSD-style | ~10.2K | Pure-Python page ops, basic text extraction |
| [GROBID](https://github.com/grobidOrg/grobid) | Apache-2.0 | ~5.1K | The reference tool for scholarly PDF → TEI-XML (header, sections, references); runs as a Java service |
| [papermage](https://github.com/allenai/papermage) | Apache-2.0 | ~0.8K | AllenAI scientific-paper structure library (low activity) |

¹ marker's repo license is Apache-2.0, but its bundled model weights carry an
additional non-commercial rider for large companies (check
`datalab-to/marker` README before relying on it commercially; fine for this
open-source tool).

## Assessment against our needs

Requirements: section-aware output (abstract, methods, limitations, …),
Python-native preferred, permissive license, works on cardiology journal
PDFs (two-column layouts, tables, figures).

- **docling** — best fit. MIT, pure-Python install, actively maintained,
  outputs a structured document model (headings/sections/tables) that maps
  cleanly onto "section-aware extraction". Heavier than pdfplumber (ML layout
  models) but fine at 10–75 papers.
- **GROBID** — best scholarly-specific accuracy (identifies sections,
  affiliations, references as TEI-XML) but requires running a Java service
  (Docker), which complicates an easy-to-install open-source tool.
- **PyMuPDF** — fast and excellent, but **AGPL-3.0** would constrain the
  project's own license choice; avoid unless we choose AGPL.
- **pdfplumber/pypdf** — permissive and light, but no layout/section
  understanding; we'd rebuild sectioning ourselves.
- **marker** — strong output quality; weight-license rider is a caveat.
- **papermage** — conceptually ideal, practically low-maintenance risk.

## Recommendation

**docling** as the primary parser (MIT, section-aware, pip-installable),
behind a small internal `parse_pdf(path) -> ParsedPaper` seam so GROBID or
marker can be swapped in if section detection on cardiology journals proves
weak. Validate on ~5 real cardiology PDFs before committing further.
