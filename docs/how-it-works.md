# How the app works mechanically

A code-level walkthrough of what actually happens when you run the Research Gap
Dashboard on a corpus, with **the parsing stage in depth**. This is the "how",
file by file; for the catalogue of stages/commands/artifacts see
`docs/FUNCTIONALITY.md`, for the "why" see `CONTEXT.md` and `docs/adr/`.

Every stage is a plain Python function that **reads the previous stage's artifact
from `<corpus>/artifacts/` and writes its own**. Nothing is held in a database or
a long-running process; the artifacts on disk *are* the state. Every LLM/API call
is cached by its inputs, so re-running a stage over the same corpus is free and
deterministic (`CODING_STANDARDS.md` > Research integrity, ADR 0001).

```
ingest ─▶ parse ─▶ extract ─▶ aggregate ─▶ detect ─▶ (Streamlit dashboard)
                     │             │
                     ▼             │
                  explain          │
  ingest --resolve ───────────────┴─▶ retrieve (out-of-corpus candidates)
```

CLI entry point: `research-gap-dashboard <command> <corpus>` (run under `uv`).

---

## 0. Corpus layout (`corpus_layout.py`, `ingest.py`)

A corpus is a directory:

```
<corpus>/
  corpus.bib or a paper list   # bibliographic records (DOIs, titles, years…)
  papers/*.pdf                 # one full-text PDF per Paper
  artifacts/                   # every stage writes its JSON here
  paper-data/                  # one <key>.parsed.json per Paper (parse stage)
  judgments/                   # dashboard-only: verdicts, chats, journal
```

**Ingest** pairs each bibliographic entry with its PDF by citation key and writes
`artifacts/corpus-manifest.json` — the list of `Paper`s (citation key, DOI, title,
year, pdf path) plus any unmatched entries and orphan PDFs. With `--resolve` it
also calls a scholarly API (OpenAlex by default, PubMed optional; see
`sources.py`) to fill in authors, `referenced_works`, and `cited_by_count`.
Everything downstream reads this manifest; it never re-reads the `.bib`.

---

## 1. Parsing — `parsing.py` (the core of this document)

**Goal:** turn each Paper's PDF into **labeled, plain-text sections** that the
Extraction stage can quote from verbatim. The output model is `ParsedPaper`:

```python
ParsedPaper(
  source_pdf: Path,
  sections: list[ParsedSection],   # each: label, heading, text
  parser: str,                     # which parser in the chain produced this
)
```

`SectionLabel` is a fixed vocabulary: `front_matter, abstract, introduction,
methods, results, discussion, conclusion, limitations, future_work,
acknowledgements, references, other`. **Nothing is ever dropped** — any heading
that does not match a known kind becomes `other`.

### 1a. The parser seam and the fallback chain

The pipeline only ever sees the `PdfParser` protocol (`parse(pdf) -> ParsedPaper`);
it never imports a PDF library directly. The shipped default (`default_parser()`)
is a **`FallbackParser` chain of three parsers, tried in order** (ADR 0005,
`docs/research/pdf-parsing-library.md`):

1. **`DoclingParser` (docling)** — layout-aware PDF → Markdown. The primary
   parser; recovers real headings and structure.
2. **`DoclingParser(ocr=True)` (docling-ocr)** — docling with OCR enabled, for
   scanned / image-only PDFs the text pipeline returns empty.
3. **`PlainTextPdfParser` (pdfplumber)** — last-resort raw text extraction. It
   recovers *no* structure (all text lands in one `other` section), but a Paper
   with a readable text layer can still be extracted.

The chain's rule: try a parser; if it **raises** or returns **near-empty text**
(fewer than `MIN_USABLE_CHARS = 200` real characters), skip it and try the next.
The first parser that clears the threshold wins, and **which parser won is
recorded** on `ParsedPaper.parser` for reproducibility. If *every* parser fails,
the chain raises `ParserChainError` — which the stage records as a per-Paper
failure (see 1e), never aborting the run.

Each concrete adapter imports its library **lazily** (inside the function), so the
heavy `docling`/`pdfplumber` deps are only needed for the `pdf` optional extra,
and the test suite parses offline by injecting fake `convert`/`extract_text`
callables.

### 1b. PDF → Markdown

docling does the hard work: `DocumentConverter().convert(pdf).document
.export_to_markdown()` returns the paper as Markdown with ATX (`#`) headings.
OCR mode is the same call with `PdfPipelineOptions.do_ocr = True`. pdfplumber,
by contrast, just concatenates `page.extract_text()` across pages — no headings.

### 1c. Markdown → labeled sections (`_sections_from_markdown`)

The Markdown is split into sections **at its ATX headings**:

- Walk the Markdown line by line. A line matching `^#{1,6}\s+heading$` starts a
  new section; everything until the next heading is that section's body.
- Each section is `flush()`ed into a `ParsedSection(label, heading, text)`.
- The **first** block before any heading becomes `front_matter`; later headless
  blocks become `other`. So title/author blocks and stray text are kept, labeled,
  never discarded.

### 1d. Heading → label mapping (`_label_for`, `_LABEL_KEYWORDS`)

Each heading is lower-cased and matched against an **ordered** keyword table, so
specific labels beat broad ones:

```
future_work   ← "future work", "future direction", "future research"
limitations   ← "limitation"
abstract      ← "abstract"
introduction  ← "introduction", "background"
methods       ← "method", "materials and methods", "methodology", "experimental"
results       ← "result", "findings"
discussion    ← "discussion"
conclusion    ← "conclusion", "concluding"
acknowledgements ← "acknowledg"
references    ← "reference", "bibliography", "works cited"
```

Order matters: "future directions" is checked before "discussion" so a
future-work heading is not swallowed by a broad match. Anything unmatched → `other`.

### 1e. Markdown cleaning (`_unescape_markdown`) — why this matters

Section text is cleaned before storage, because docling's Markdown encodes
punctuation in ways that are **export artifacts, not the paper's prose**, and
those break the verbatim-Evidence matching the Extraction stage depends on:

- **HTML comment placeholders** like `<!-- image -->` are removed (pure noise).
- **HTML entities** are decoded (`P&lt;0.01` → `P<0.01`, `R&amp;D` → `R&D`).
- **Backslash escapes** are undone (`cel\_miR-39` → `cel_miR-39`).

The LLM later quotes the *decoded* prose, so cleaning here is what lets per-fact
Evidence be verified character-for-character against the stored text (ADR 0004).

### 1f. The stage runner, output files, and failure isolation (`parse_corpus`)

`parse_corpus(root)` reads the manifest, parses each Paper **independently**, and
writes one file per Paper to `paper-data/<citation_key>.parsed.json` (the
serialized `ParsedPaper`). A PDF that fails is appended to the report's failures
and **the run carries on** (ADR 0006 — surface, don't block). The stage also
writes `artifacts/parse-report.json` (`ParseReport`: the files written + the
failures), which the dashboard's "Needs Attention" queue and incompleteness
banner read.

### 1g. The parse funnel (`diagnose.py`)

Parsing can succeed yet still not yield usable structure, so `diagnose` reports a
per-stage funnel **PDFs → parsed → sectioned → extracted** and why each Paper
fell out:

- **not parsed** — no `.parsed.json` (the parser chain failed on this PDF).
- **near_empty** — parsed, but under `NEAR_EMPTY_CHARS = 200` real characters.
- **collapsed** — parsed, but *every* section is unlabeled (`front_matter`/`other`):
  docling recognised no paper structure (common for odd two-column scans).
- **sectioned** = parsed AND not near-empty AND not collapsed — real, labeled
  body text, the only Papers worth extracting from.

On the 50-paper demo run this reads: 50 PDFs → 50 parsed → 46 sectioned → 45
extracted = **90% end-to-end**; the shortfall is surfaced honestly, never hidden.

---

## 2. Extract — `extract.py`

For each **sectioned** Paper, one LLM call (Anthropic, via the `llm.py` cached
seam) pulls the structured Extraction: research question, methods,
populations, datasets, key findings, limitations, future work — each fact carrying
an **Evidence** passage. Then the integrity gate (ADR 0004): **every fact's
Evidence quote is verified verbatim against the Paper's parsed `full_text`**; a
fact whose quote cannot be found is **dropped as unverifiable**, and a Paper with
zero verifiable facts is **rejected**. Output: `artifacts/extractions.json`.
(On the demo run: 45/50 extracted, 5 rejected by this gate, 58 facts dropped.)

## 3. Aggregate — `aggregate.py`

Each Paper's free-text facts are **normalized onto the shared taxonomy axes**
(Topic, Method, Population, Dataset) with an LLM call, so Papers become
comparable. Every mapping records axis, the Paper's original term, the assigned
category, and the source Evidence; a phrase that fits no category is surfaced as
an **unmapped term** (for taxonomy editing), never silently dropped. Output:
`artifacts/normalized_facts.json`.

## 4. Detect — `detect.py`

Builds the Coverage Matrices from the normalized facts and **cards every sparse
cell** as a Candidate Gap — Knowledge, Coverage, and Unanswered-Limitation types
(the last via the limitation grouping + follow-up matcher in `limitations.py`).
Output: `artifacts/candidate_gaps.json`. Gaps are candidates for human judgment,
with a confidence and the reasoning attached — never verdicts.

## 5. Retrieve — `retrieval.py` (optional, needs `ingest --resolve`)

Ranks each Paper's OpenAlex `referenced_works` by in-corpus citation overlap,
resolves the top survivors back to real works, and proposes **out-of-corpus
papers** the corpus probably should contain. Output: `artifacts/retrieval_gaps.json`.
This is the only stage that looks beyond the corpus.

## 6. Dashboard — `dashboard/` (Streamlit)

Reads the finished artifacts and renders them; it holds **no analysis logic and
imports no pipeline stage** (ADR 0002). It re-reads each Paper's
`paper-data/*.parsed.json` only to **widen an Evidence quote to its surrounding
paragraph** for display (`evidence_context.py`) — the anchor stays the verified
ground truth; the code only adds context it did not invent. Judgments, chats, and
the research journal persist under `judgments/`.

---

## Why it is shaped this way

- **Artifacts, not a pipeline object.** Each stage is independently runnable and
  inspectable; a rerun from any point reads JSON off disk. This is what makes the
  funnel diagnostic and the "Needs Attention" honesty surface possible.
- **Seams hide heavy libraries.** The `PdfParser` and `SourceAdapter` protocols
  (and the `llm.py` client) keep docling, pdfplumber, httpx, and Anthropic behind
  injectable interfaces, so `just test` runs fully offline with fakes.
- **Surface, don't block (ADR 0006).** A single bad PDF, an unverifiable fact, or
  an unmapped term is recorded and shown, never allowed to abort a run or vanish
  silently.

## File map

| Concern | Module |
|---|---|
| Corpus layout & manifest | `corpus_layout.py`, `ingest.py` |
| **Parsing (PDF → sections)** | **`parsing.py`** |
| Funnel diagnostic | `diagnose.py` |
| Fact extraction + Evidence gate | `extract.py` |
| Taxonomy normalization | `aggregate.py`, `taxonomy.py` |
| Gap detection | `detect.py`, `limitations.py` |
| Out-of-corpus retrieval | `retrieval.py` |
| Scholarly-API adapters | `sources.py` |
| Cached LLM seam | `llm.py` |
| Dashboard (read-only views) | `dashboard/` |
