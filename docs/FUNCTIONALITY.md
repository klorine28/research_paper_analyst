# App functionality reference

A single, current map of everything the Research Gap Dashboard does today: the
pipeline stages, their CLI commands and artifacts, the source-adapter seam, and
the dashboard pages. Keep this file in step with the code — when a slice adds a
command, artifact, or page, add a row here in the same PR.

For *why* things are the way they are, see `CONTEXT.md` (vocabulary),
`docs/adr/` (decisions), and `docs/BRIEF.md` (open items). For loose findings
not yet functionality, see `docs/HANDOFF.md`.

---

## The pipeline at a glance

A run flows left to right; each stage reads the previous stage's artifact from
`<corpus>/artifacts/` and writes its own. Every LLM/API call is cached by its
inputs, so reruns over the same Corpus are free and reproducible
(`CODING_STANDARDS.md` > Research integrity).

```
ingest ─▶ parse ─▶ extract ─▶ aggregate ─▶ detect ─▶ (dashboard review)
                     │              │
                     ▼              │
                  explain           │
   ingest --resolve ────────────────┴─▶ retrieve (out-of-corpus candidates)
```

CLI entry point: `research-gap-dashboard <command> <corpus>` (defined at
`[project.scripts]` in `pyproject.toml`). Always run under uv, e.g.
`uv run research-gap-dashboard ingest ./corpora/cardiology`.

---

## Corpus directory layout

A Corpus is one directory (see `docs/corpus-layout.md` and
`src/research_gap_dashboard/corpus_layout.py`):

| Path | Holds |
| --- | --- |
| `papers/` | the Papers' PDF files |
| `<name>.bib` or `dois.txt` | the paper list (BibTeX or one DOI per line) |
| `paper-data/` | per-Paper derived data (parsed sections, per-Paper explanations) |
| `artifacts/` | stage output artifacts (below) |
| `judgments/` | accept/reject judgments and (future) chat history |
| `.llm-cache/` | on-disk LLM response cache |

---

## Stages, commands, and artifacts

| Stage | Command | Reads | Writes (`artifacts/`) | LLM? | Network? |
| --- | --- | --- | --- | --- | --- |
| Ingest | `ingest <corpus> [--resolve] [--source openalex\|pubmed] [--mailto EMAIL]` | paper list + `papers/*.pdf` | `corpus-manifest.json` | no | only with `--resolve` |
| Parse | `parse <corpus>` | manifest + PDFs | per-Paper sectioned text in `paper-data/` | no | no |
| Extract | `extract <corpus> [--tier default\|cheap]` | manifest + parsed text | `extractions.json` | yes | yes (uncached) |
| Explain | `explain <corpus> [--tier default\|cheap]` | manifest + parsed text | per-Paper `*.explanation.json` in `paper-data/` | yes | yes (uncached) |
| Aggregate | `aggregate <corpus> [--taxonomy FILE ...] [--tier ...]` | extractions + taxonomies | `normalized_facts.json` | yes | yes (uncached) |
| Detect | `detect <corpus> [--tier ...]` | manifest + extractions + normalized facts | `candidate_gaps.json` | yes (limitations only) | yes (uncached) |
| Retrieve | `retrieve <corpus> [--limit N] [--min-overlap N] [--mailto EMAIL]` | manifest (needs `ingest --resolve`) | `retrieval_gaps.json` | no | yes (OpenAlex) |
| Taxonomy | `taxonomy [FILE]` | a taxonomy file | — (validation only) | no | no |

Every command exits `0` on success and `1` on a handled error; the CLI only
parses args and maps errors to exit codes (`src/research_gap_dashboard/cli.py`).

### Ingest — `ingest.py`
Pairs each paper-list entry with its PDF (by DOI, then citation key) and writes
the `CorpusManifest` (`Paper`s, plus `unmatched_entries` and `orphan_pdfs`).
Enforces the 10–75 Paper envelope. With `--resolve`, enriches each Paper with
canonical metadata (title, year, venue, authors, `openalex_id`,
`referenced_works`, `cited_by_count`) via a source adapter. Public reader:
`read_manifest(root)`.

### Parse — `parsing.py`
Turns each Paper's PDF into sectioned text under `paper-data/` (docling behind a
`PdfParser` seam). Reports per-Paper failures rather than aborting. Reader:
`read_parsed_paper(root, citation_key)`.

### Extract — `extract.py`
LLM-extracts structured facts (research question, methods, populations,
datasets, key findings, stated limitations, future work) with **verified
Evidence** — each quoted passage is checked against the parsed text. Writes
`extractions.json`. Reader: `read_extractions(root)`. (Verification is currently
strict on real PDFs — see `docs/HANDOFF.md`.)

### Explain — `explain.py`
LLM-writes a plain-language explanation of each Paper's experiment in two
registers — a **domain** explanation for a researcher in the field and a **lay**
explanation for a non-specialist — grounded only in that Paper's parsed text
(the prompt sees the text alone and is told to describe only what the Paper
says). Stores one `<citation_key>.explanation.json` per Paper under
`paper-data/`, with the prompt version and model tier behind each one. Reports
per-Paper failures rather than aborting. Reader:
`read_paper_explanation(root, citation_key)`.

### Aggregate — `aggregate.py`
Maps each Paper's free-text facts onto the shared axis taxonomies (Topic,
Method, Population, Dataset) so Papers can be compared in the Coverage Matrix.
Every assignment records axis, original term, assigned category, source fact,
and Evidence; unplaceable phrases surface as unmapped terms, never dropped.
Writes `normalized_facts.json`. Reader: `read_normalized_facts(root)`.

### Detect — `detect.py`
Builds Coverage Matrices and cards the four in-Corpus gap signals into
`candidate_gaps.json` (reader: `read_candidate_gaps(root)`):
- **Knowledge Gap** — sparse Topic × Topic cell.
- **Coverage Gap** — sparse Topic × Method / Population / Dataset cell.
- **Unanswered Limitation** — a limitation/future-work group no later Paper
  addressed (LLM-grouped).

Matrix detection is deterministic; only cells whose two categories both occur
in the Corpus count. Sparse threshold is provisional: empty cells only under 25
Papers, empty-or-single at 25+. Every card carries confidence, reasoning,
denominator, and in-Corpus Evidence.

### Retrieve — `retrieval.py`
The one gap type that looks **outside** the Corpus (`CONTEXT.md` > Gap Type).
Ranks the works the Corpus's Papers cite by **citation overlap** (how many
Corpus Papers reference each), drops works already in the Corpus (by OpenAlex
id, then by DOI after resolution), keeps those with overlap ≥ `--min-overlap`
(default 2), and resolves the top `--limit` (default 20) against OpenAlex.
Writes `retrieval_gaps.json` — a separate artifact, clearly labelled
out-of-corpus, whose candidates carry **no in-Corpus Evidence**. Reader:
`read_retrieval_gaps(root)`. Requires that `ingest --resolve` recorded the
OpenAlex citation graph.

---

## Source-adapter seam — `sources.py`

One `SourceAdapter` protocol (`resolve(doi) -> WorkRecord | None`) with two v1
implementations, so ingest and retrieval reach scholarly APIs behind one seam
and the suite runs offline by injecting a fake `fetch`:

- **`OpenAlexAdapter`** — primary source. Resolves by DOI, and additionally
  exposes `fetch_work(openalex_id)` (the citation graph Retrieval Gap detection
  ranks over — OpenAlex-specific, not part of the protocol).
- **`PubMedAdapter`** — second source, via NCBI E-utilities (esearch →
  esummary). Selected at ingest with `--source pubmed`.

---

## Dashboard — `dashboard/`

A thin Streamlit read layer over the on-disk artifacts. It **only reads**
artifacts and imports no pipeline stage (ADR 0002), so the front end stays
swappable. Run it:

```shell
just dashboard            # uv run streamlit run src/research_gap_dashboard/dashboard/app.py
```

Point it at the folder holding your corpus directories with
`RESEARCH_GAP_CORPORA_DIR` (default `./corpora`). The sidebar picks a Corpus and
a page.

| Page | Module | Needs artifact | Shows |
| --- | --- | --- | --- |
| Corpus Overview | `overview.py` | `corpus-manifest.json` | scope statement, paper/venue/year metrics, papers-per-year bar, venue table, exclusions (unmatched entries, orphan PDFs) |
| Field Meta-Analysis | `meta_analysis.py`, `coverage.py` | `corpus-manifest.json` (+ `normalized_facts.json`, `candidate_gaps.json`) | a gallery of six graphs: citation network, Topic and Method co-occurrence (with ranked missing pairs that jump to Gap Cards), Trends (emerging/abandoned Topics), an author collaboration table, and limitation follow-up status; each carries its question, gap lens, denominator, caveat and a data-table fallback |
| Gap Cards | `gaps.py`, `judgments.py` | `candidate_gaps.json` | one card per Candidate Gap with type, confidence + reasoning, denominator, Evidence passages, and **accept/reject/clear** buttons that persist to `judgments/` |
| Unanswered Limitations | `limitations.py` | `candidate_gaps.json` | every limitation group (addressed or not) with its follow-up count, source Papers, and verbatim source passages; unanswered groups called out from addressed ones |
| Retrieval Gaps | `retrieval.py` | `retrieval_gaps.json` | out-of-corpus candidates ranked by citation overlap, kept visually and verbally separate (warning banner) as candidates for improving the search, not evidence-linked gaps |
| Paper Explainer | `explainer.py` | `corpus-manifest.json` + per-Paper `*.explanation.json` | a Paper picker, then that Paper's experiment explained in **domain** and **lay** language, next to a link back to the Paper's metadata (title, venue, DOI) |
| Paper Comparison | `comparison.py` | `corpus-manifest.json` + `extractions.json` + `normalized_facts.json` | pick 2–15 Papers; compares them field by field (research question, methods, population, datasets, findings, limitations), highlights **agreements/differences** and a **mini coverage matrix** of blind spots (grounded in shared normalized categories), and splits 6–15 Papers into summarized sets of ≤5 before comparing |
| Conversational Analytics | `analytics.py`, `chat_history.py` | `corpus-manifest.json` (+ `candidate_gaps.json` for the summary) | a chat grounded in the Corpus and a **narrative summary** for a "gaps in the literature" section; both cite **only Corpus Papers** (every generated citation checked against the Corpus, ungrounded ones dropped and flagged), and the conversation persists to `judgments/chat_history.json` |
| Verify Extractions | `verify.py`, `extraction_review.py` | `extractions.json` + parsed text | per-Paper review of each extracted fact against its Evidence: approve, edit, flag, remove, or add a fact; edits must quote the parsed text verbatim; saved as a `judgments/` overlay (ADR 0003) |
| Needs Attention | `attention.py`, `parse_corrections.py` | manifest + `parse-report.json` + `extractions.json` | the Papers that fell out (unparsed, extraction rejected, facts dropped) with reasons, and a paste-corrected-text box that writes a `judgments/` overlay (ADR 0006); the same read model drives the incompleteness banner on every page |

Supporting modules: `artifacts.py` (discover corpora, load/guard each
artifact), `text.py` (all user-facing strings, kept in one place for future
translation), `app.py` (shell, navigation, rendering).

Conversational Analytics is the one page that generates at view time rather than
reading a finished artifact. To keep the dashboard package free of the LLM seam
(ADR 0002), the grounded-generation machinery lives in the top-level
`analytics.py` (grounding context, prompts, citation enforcement, and the cached
client); `app.py` maps the artifact read models onto it and invokes it. A
missing `ANTHROPIC_API_KEY` degrades the page to a clear message, not a crash.

### Not yet built (dashboard)
- Driving pipeline commands from the app (proposed in `docs/HANDOFF.md`; needs
  an ADR because it touches ADR 0002's read-only boundary).

---

## Configuration

Read once from the project-root `.env` (see `README.md` and ADR 0001):
`ANTHROPIC_API_KEY` (unset ⇒ offline/recorded runs only), `LLM_DEFAULT_MODEL`,
`LLM_CHEAP_MODEL`, `LLM_MAX_TOKENS`. `just test` never calls the API or network.

## Canonical dev commands

`just sync` (install), `just test`, `just lint`, `just format-check`,
`just type-check`, `just dashboard`. One test:
`uv run pytest path/to/test_file.py -k test_name`.
