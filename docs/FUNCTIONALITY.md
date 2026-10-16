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
                     │              │           │
                     ▼              │           └─▶ saturate --gap ID (wider literature)
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
| `judgments/` | accept/reject judgments, chat history, the journal and the resume point |
| `.llm-cache/` | on-disk LLM response cache |

---

## Stages, commands, and artifacts

| Stage | Command | Reads | Writes (`artifacts/`) | LLM? | Network? |
| --- | --- | --- | --- | --- | --- |
| Ingest | `ingest <corpus> [--check] [--resolve] [--source openalex\|pubmed] [--mailto EMAIL]` | paper list + `papers/*.pdf` | `corpus-manifest.json` | no | only with `--resolve` |
| Parse | `parse <corpus>` | manifest + PDFs | per-Paper sectioned text in `paper-data/` | no | no |
| Extract | `extract <corpus> [--tier default\|cheap]` | manifest + parsed text | `extractions.json` | yes | yes (uncached) |
| Explain | `explain <corpus> [--tier default\|cheap]` | manifest + parsed text | per-Paper `*.explanation.json` in `paper-data/` | yes | yes (uncached) |
| Aggregate | `aggregate <corpus> [--taxonomy FILE ...] [--tier ...]` | extractions + taxonomies | `normalized_facts.json` | yes | yes (uncached) |
| Detect | `detect <corpus> [--tier ...]` | manifest + extractions + normalized facts | `candidate_gaps.json` | yes (limitations only) | yes (uncached) |
| Retrieve | `retrieve <corpus> [--limit N] [--min-overlap N] [--mailto EMAIL]` | manifest (needs `ingest --resolve`) | `retrieval_gaps.json` | no | yes (OpenAlex) |
| Saturate | `saturate <corpus> (--gap ID \| --accepted \| --auto) [--taxonomy FILE ...] [--tier cheap\|default]` | candidate gaps + manifest + normalized facts + taxonomies (+ Alias Overlay) | appends to `saturation_checks.json` | optional: query variants only on zero hits (cheap tier), and screening of the top hits (both skipped without a key) | yes (OpenAlex + PubMed, LLM) |
| Add suggested | `add-suggested <corpus> --route retrieval\|saturation (--doi D \| --pmid P \| --openalex-id W) [--title T] [--year Y] [--upload PDF] [--mailto EMAIL]` | a Retrieval Gap or saturation hit + paper list | its PDF in `papers/`, a paper-list entry, `suggested_papers.json`; prints the outcome as JSON (exit 2 when pending) | no | yes (OpenAlex + the open-access host), unless `--upload` |
| Mark unavailable | `mark-unavailable <corpus> --route ... (--doi ... ) --reason TEXT` | a suggested work | `suggested_papers.json` | no | no |
| Taxonomy | `taxonomy [FILE]` | a taxonomy file | — (validation only) | no | no |

Every command exits `0` on success and `1` on a handled error; the CLI only
parses args and maps errors to exit codes (`src/research_gap_dashboard/cli.py`).

### Saturate — `saturate.py`
Tries to disprove one Knowledge or Coverage Gap against the wider literature
(ADR 0009). Each concept of the cell is searched by its taxonomy label and
aliases (MeSH inversions turned back, Corpus-approved aliases included) plus
the Corpus's own mapped terms; PubMed also gets the MeSH heading. One boolean
query per source, recorded verbatim; each term records its origin (label,
alias, Corpus term). Only when the base queries find no closing work, the
cheap-tier LLM proposes rephrased terms and a labelled adversarial second
attempt runs (`attempt: "llm_variant"` on its searches and hits; model and
prompt version on `second_attempt`; cached in `.llm-cache/` by inputs, model
and prompt version). Without an API
key or on an LLM error the check still completes and `second_attempt.note`
says why. A hit counts only when its title or
abstract mentions a term of both concepts (deterministic screening); works
already in the Corpus are dropped. An LLM (cheap tier by default) then screens
the top 10 matches from title and abstract: `addresses` / `partial` / `no`, with
a one-line reason; a hit judged `no` stops counting. Every hit keeps both
verdicts, and the check records the model, tier and prompt version. Without
`ANTHROPIC_API_KEY` the LLM step is recorded as not run and every match counts;
a hit the LLM did not screen (past the top 10, or its call failed) still counts.
The result is `possibly_addressed`,
`no_closing_work_found` (dated, with sources and queries) or `not_run` (no
source reachable; exit code 2). Never confirms a gap, never changes confidence.
`--accepted` ("check all accepted") runs the check on every gap accepted on the
Gap Cards page, in artifact order: a gap whose latest check ran (not `not_run`)
within the last 30 days keeps that snapshot and is not searched again; a gap
that can't be checked (no longer in the artifact) is skipped with the reason.
Its checks are recorded with `trigger: "accepted"`; exit code 2 if any of them
did not run. A single `--gap` check always searches live (ADR 0009 §9).
Each run appends a dated snapshot; earlier snapshots are never rewritten. Hits
are out-of-Corpus works: never cited, never Evidence. An Unanswered Limitation
is checked for follow-up from its verbatim source statements: the Topic
categories they name (by label, alias or Corpus term; acronyms find a Topic but
are not searched; the Corpus's dominant Topic when none is named) ANDed with
the statements' key phrases (filler and boilerplate stripped). Its hits must
also not predate the earliest source Paper; older ones are kept as
`predates_statement`. The snapshot records the `statement_ids` it was built
from. Retrieval Gaps are refused: they are out-of-Corpus works already. Public
reader: `read_saturation_checks(root)`.

`saturate --auto` (issue #89) checks every high-confidence gap, strongest first
(cell gaps by L1 probability, then Unanswered Limitations by later Papers), at
most 20 live per run; the rest are logged as `left_for_on_demand`. A gap whose
latest snapshot ran the same base query on every source, without error, in the
last 30 days is reused (`cached`, not counted against the cap); a `not_run`
snapshot is never reused. A source that is unreachable or rate-limited (429,
5xx) is not asked again in that run, so an offline run completes with every
check `not_run` (exit code 2). Its snapshots carry `trigger: "auto"`, and each
run is appended to `auto_runs` in `saturation_checks.json` with every gap's
outcome. `SATURATE_AUTO=off` switches it off. The Pipeline page's detect button
chains `saturate --auto` after `detect`; `detect` itself never searches. Not
yet: `--accepted`.

### Grow — `grow.py`
The one path by which a suggested Paper (a Retrieval Gap candidate or a
saturation hit) enters the Corpus (ADR 0009 §11-12, #91). `add-suggested` looks
the work up on OpenAlex (`OpenAlexAdapter.open_access`: best open-access
location, every other open-access location, then `oa_url`) and downloads the
first URL that really serves a PDF; `--upload` supplies the PDF instead. The PDF
goes into `papers/` with a fresh citation key, a BibTeX entry (or DOI line) is
appended to the paper list, and both are kept only if ingest's pairing check
then pairs them; otherwise everything is rolled back. Without a PDF the work is
**pending**: recorded, but in neither the paper list nor `papers/`, so it is in
no denominator. A paper is never added metadata-only. `mark-unavailable` records
the reason and date; an upload later reverses it. The 10-75 envelope is checked
first, and a work already listed (by DOI or OpenAlex id, whichever route
suggested it) is never added twice. Every suggestion, its route(s), status and
dated history live in `suggested_papers.json` at the Corpus root. Public reader:
`read_suggested_papers(root)`. Re-running the stages is the caller's job: the
dashboard chains `ingest → parse → extract → aggregate → detect` after an add.

### Ingest — `ingest.py`
Pairs each paper-list entry with its PDF (by DOI, then citation key) and writes
the `CorpusManifest` (`Paper`s, plus `unmatched_entries` and `orphan_pdfs`).
Enforces the 10–75 Paper envelope. With `--resolve`, enriches each Paper with
canonical metadata (title, year, venue, authors, `openalex_id`,
`referenced_works`, `cited_by_count`) via a source adapter. Public reader:
`read_manifest(root)`. `check_corpus(root)` (CLI: `ingest --check`) does the same
pairing and envelope check but writes nothing, printing a `PairingCheck` as JSON
on stdout (exit 1 outside the envelope); the dashboard's Add Papers panel reads
it after every upload.

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
On the Method axis (study design only, ADR 0011) non-design phrases are
recorded as "not a study design" rather than unmapped.
Writes `normalized_facts.json`. Reader: `read_normalized_facts(root)`.

### Detect — `detect.py`
Builds Coverage Matrices and cards the four in-Corpus gap signals into
`candidate_gaps.json` (reader: `read_candidate_gaps(root)`):
- **Knowledge Gap** — sparse Topic × Topic cell.
- **Coverage Gap** — sparse Topic × Method / Population / Dataset cell.
- **Unanswered Limitation** — a limitation/future-work group no later Paper
  addressed (LLM-grouped).

Matrix detection is deterministic; only cells whose two categories both occur
in the Corpus count. One candidate rule at every Corpus size: a cell is a
candidate if it is empty, or holds one Paper where more than one was expected.
Confidence starts from L1, the hypergeometric lower tail `P(X ≤ observed)`
(≤ 0.05 High, ≤ 0.20 Medium, else Low), a ranking score that cards explain in
counts and never call significant. The artifact records the rule, each cell's
`l1_probability`, and the smallest equal category sizes that can rank High or
Medium at this Corpus size. Every card carries confidence, reasoning,
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

Both adapters also satisfy a `TopicSearchSource` protocol
(`search(query, limit) -> list[WorkRecord]`) for saturation checks (ADR 0009):
OpenAlex searches `/works` and rebuilds each abstract from its inverted index;
PubMed runs esearch by relevance, then efetch (XML) for abstracts. Search hits
carry `openalex_id`, `doi` and/or `pmid`, `title`, `year` and `abstract` (empty
when the source has none). `SourceIdentity.from_env()` reads how the tool
identifies itself: `OPENALEX_MAILTO` (polite pool) and `NCBI_API_KEY`.

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
a page. Navigation follows the guided path **1 Scope → 2 Build → 3 Trust → 4
Explore → 5 Judge → 6 Grow → 7 Write** (issue #76): the sidebar groups the pages
under their numbered steps and the phases Set up (Scope, Build), Check (Trust),
Analyse (Explore, Judge) and Extend (Grow, Write); every page carries a step bar
(completed steps ticked, the current step highlighted) and **← Previous / Next →**
buttons naming the adjacent step. Every page stays reachable from the sidebar
at any time. Step completion and the home screen's recommendation come from
`guided_flow.py`, read from the scope record, the pipeline status, the fallout
and the gap verdicts. The furthest step reached on each Corpus and when it was
last opened persist to `judgments/resume_point.json` (`resume.py`, issue #83):
a reopened dashboard starts on the Corpus opened most recently, and its Home
screen offers to **resume** at the furthest step.

**Soft Trust gate** (issue #77, `trust_gate.py`). Once `extract` has run, a
Corpus where Papers fell out or lost facts shows a lock screen instead of the
Explore, Judge, Grow and Write pages. The screen states the denominator ("these
gaps come from 10 of 15 Papers") and offers two ways on: fix the fallout on
Needs Attention, or acknowledge the denominator and continue. The
acknowledgement is saved to `judgments/trust-acknowledgement.json` with the
Corpus state it covers (Papers reached, total, Papers missing facts). If that
state changes, the gate asks again. The incompleteness banner stays on every
page after acknowledging, and a complete Corpus never sees the gate. An
acknowledged Corpus moves Home's recommendation past Trust, but Trust is only
ticked when the Corpus is complete.

| Page (step) | Module | Needs artifact | Shows |
| --- | --- | --- | --- |
| Home | `guided_flow.py`, `resume.py` | `corpus-manifest.json` (+ stage artifacts, `judgments/gap_judgments.json`, `judgments/resume_point.json`) | **Resume** at the furthest step reached, stating the saved verdicts and journal entries (once the Corpus was worked on); **Recommended next step** with its reason (no scope → Scope; a core stage not run or stale → Build; Papers fell out → Trust; no verdicts yet → Explore; undecided gaps → Judge; otherwise Write) and a button to it; Corpus health (Papers, Papers reached, pipeline stages done, Candidate Gaps, accepted, undecided); the seven steps with completed ones ticked; once detect has run twice, **Changed since last run** (gaps closed and the new Papers covering them, new gaps, verdicts that no longer reach a gap, over the Papers before and now), also shown after any detect run |
| Corpus Overview (1 Scope) | `overview.py` | `corpus-manifest.json` | scope statement (with how many suggested papers were marked unavailable), paper/venue/year metrics, papers-per-year bar, venue table, exclusions (unmatched entries, orphan PDFs) |
| Pipeline (2 Build) | `pipeline_status.py`, `run_cost.py`, `pipeline_runner.py` | `corpus-manifest.json` (+ every stage artifact, `llm-usage.json`) | each stage as done / not run / stale (an input newer than its artifact), with a run or re-run button per stage (detect's also runs `saturate --auto` after it); a collapsed **Run details** with each LLM stage's estimated cost and its last run's actual cost; a run estimated above `LLM_COST_CONFIRM_ABOVE` asks for confirmation |
| Field Meta-Analysis (4 Explore) | `meta_analysis.py`, `coverage.py` | `corpus-manifest.json` (+ `normalized_facts.json`, `candidate_gaps.json`) | a gallery of six graphs: citation network, Topic and Method co-occurrence (with ranked missing pairs that jump to Gap Cards), Trends (emerging/abandoned Topics), an author collaboration table, and limitation follow-up status; each carries its question, gap lens, denominator, caveat and a data-table fallback |
| Gap Cards (5 Judge) | `gaps.py`, `judgments.py` | `candidate_gaps.json` | one card per Candidate Gap with type, confidence + reasoning, denominator, Evidence passages, and **accept/reject/clear** buttons that persist to `judgments/`; Knowledge, Coverage and Unanswered Limitation cards carry a wider-literature badge (from `saturation_checks.json`), the queries/hits drill-down, and a **Check wider literature** button that runs `saturate --gap`; above the cards, **Check all accepted gaps** runs `saturate --accepted` (counting only gaps not checked in the last 30 days, with its cost estimate going through the `LLM_COST_CONFIRM_ABOVE` confirmation), and a **Possibly addressed only** toggle beside the confidence filter narrows the list to gaps whose latest check found possibly closing work, saying how many gaps it hides; each hit can be **added to the Corpus** (open-access fetch, upload, or mark unavailable), and a hit that is also a Retrieval Gap candidate is marked so |
| Unanswered Limitations (4 Explore) | `limitations.py` | `candidate_gaps.json` | every limitation group (addressed or not) with its follow-up count, source Papers, and verbatim source passages; unanswered groups called out from addressed ones |
| Retrieval Gaps (6 Grow) | `retrieval.py` | `retrieval_gaps.json` | out-of-corpus candidates ranked by citation overlap, kept visually and verbally separate (warning banner) as candidates for improving the search, not evidence-linked gaps; each can be **added to the Corpus** through the same Grow path as saturation hits (open-access PDF fetched, else upload, else mark unavailable with a reason; the stages then re-run), a candidate a saturation check also returned names those gaps, and unavailable ones sit in a collapsed list |
| Paper Explainer (5 Judge) | `explainer.py` | `corpus-manifest.json` + per-Paper `*.explanation.json` | a Paper picker, then that Paper's experiment explained in **domain** and **lay** language, next to a link back to the Paper's metadata (title, venue, DOI) |
| Paper Comparison (5 Judge) | `comparison.py` | `corpus-manifest.json` + `extractions.json` + `normalized_facts.json` | pick 2–15 Papers; compares them field by field (research question, methods, population, datasets, findings, limitations), highlights **agreements/differences** and a **mini coverage matrix** of blind spots (grounded in shared normalized categories), and splits 6–15 Papers into summarized sets of ≤5 before comparing; field-by-field columns wrap at three per row so 5–15 Papers stay legible |
| Conversational Analytics (7 Write) | `analytics.py`, `fact_retrieval.py`, `fact_pool.py`, `chat_history.py`, `export.py` | `corpus-manifest.json`, `extractions.json` + `normalized_facts.json` (per-question facts), `candidate_gaps.json` and `judgments/gap_judgments.json` (summary) | a chat grounded in the Corpus, each question pulling in its relevant extracted facts and Evidence, and a **narrative summary** for a "gaps in the literature" section, built **only from accepted gaps** and stating its denominator (N accepted gaps over M Papers, with rejected and undecided counts excluded), which can be **exported** as Markdown, a BibTeX file of only the Corpus Papers it cites, and the scope-and-limits statement (question, field, what was searched, denominator, year span, unplaced entries); both cite **only Corpus Papers** (every generated citation checked against the Corpus, ungrounded ones dropped and flagged), and the conversation persists to `judgments/chat_history.json` |
| Verify Extractions (3 Trust) | `verify.py`, `extraction_review.py` | `extractions.json` + parsed text | per-Paper review of each extracted fact against its Evidence: approve, edit, flag, remove, or add a fact; edits must quote the parsed text verbatim; saved as a `judgments/` overlay (ADR 0003) |
| Needs Attention (3 Trust) | `attention.py`, `parse_corrections.py` | manifest + `parse-report.json` + `extractions.json` | the Papers that fell out (unparsed, extraction rejected, facts dropped) with reasons, and a paste-corrected-text box that writes a `judgments/` overlay (ADR 0006); the same read model drives the incompleteness banner on every page |

Supporting modules: `artifacts.py` (discover corpora, load/guard each
artifact), `text.py` (all user-facing strings, kept in one place for future
translation), `app.py` (shell, navigation, rendering).

Conversational Analytics is the one page that generates at view time rather than
reading a finished artifact. To keep the dashboard package free of the LLM seam
(ADR 0002), the grounded-generation machinery lives in the top-level
`analytics.py` (grounding context, prompts, citation enforcement, and the cached
client); `app.py` maps the artifact read models onto it and invokes it. Each
chat question retrieves its relevant Extraction facts and Evidence from the
Corpus's fact pool (`dashboard/fact_pool.py`) by deterministic keyword matching
(`fact_retrieval.py`: Paper names, field cues, rarity-weighted keywords over
statements, Evidence and normalized categories), capped at a 12,000-character
budget; when nothing matches, the model is told so and answers "not in this
Corpus". A missing `ANTHROPIC_API_KEY` degrades the page to a clear message, not a crash.

### Not yet built (dashboard)
- The rest of ADR 0007: in-app upload and Corpus creation, and buttons for the
  seam commands (apply correction and re-extract, promote gold). Running and
  re-running stages is built (Pipeline page, Gap Cards empty state).

---

## Configuration

Read once from the project-root `.env` (see `README.md` and ADR 0001):
`ANTHROPIC_API_KEY` (unset ⇒ offline/recorded runs only), `LLM_DEFAULT_MODEL`,
`LLM_CHEAP_MODEL`, `LLM_MAX_TOKENS`, `LLM_COST_CONFIRM_ABOVE` (dashboard run
confirmation threshold in US dollars, default 1.00). `just test` never calls the
API or network.

## Canonical dev commands

`just sync` (install), `just test`, `just lint`, `just format-check`,
`just type-check`, `just dashboard`. One test:
`uv run pytest path/to/test_file.py -k test_name`.
