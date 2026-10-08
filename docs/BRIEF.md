# Project Brief: Research Gap Dashboard

Starting point for the first `/grill-with-docs` session. Everything here is a
**draft**: terms move into `CONTEXT.md` and decisions into `docs/adr/` only
once the grilling session resolves them. When a section below is settled,
delete it from this file so the brief shrinks as the project firms up.

## Goal

A tool that lets a researcher point at a body of literature on a topic and get
a dashboard that clearly shows where the research gaps are, backed by data,
graphs, and short written explanations. Every gap shown must be traceable to
the papers and passages that support it.

## Users

- **Primary:** researchers (grad students, postdocs, faculty) scoping a
  literature review, thesis, or grant proposal.
- **Secondary (to confirm):** supervisors or review committees reading the
  dashboard someone else produced.

## Draft pipeline

Five stages, each with an inspectable intermediate artifact on disk so a stage
can be re-run without redoing the ones before it:

1. **Ingest:** collect papers into a Corpus (PDF upload, DOI/BibTeX list,
   and/or a search against a scholarly API).
2. **Extract:** pull structured facts from each paper: research question,
   methods, populations/samples, datasets, key findings, stated limitations,
   stated future work.
3. **Aggregate:** normalize those facts into shared categories so papers can be
   compared (e.g. map "RCT" and "randomised trial" to the same Method).
4. **Detect:** find Candidate Gaps: empty or thin cells in coverage matrices,
   limitations nobody followed up on, contradictory findings, topics that
   stopped being studied.
5. **Present:** render the dashboard.

## Draft dashboard sections

- **Corpus overview:** paper count, papers per year, venues, fields; what was
  searched and what was excluded, so the reader knows the dashboard's scope.
- **Coverage matrix:** heatmap of Topic × Method (or × Population, × Dataset).
  Empty and sparse cells are the most visual gap signal.
- **Trends:** publication volume per topic over time; emerging vs. abandoned
  lines of work.
- **Unanswered limitations:** limitations and future-work statements, grouped,
  with a count of how many later papers addressed each.
- **Contradictions (deferred past v1):** findings that disagree, side by side
  with their sources. Deferred with evidence-gap detection.
- **Paper Explainer:** each Paper's experiment explained in domain language
  and in lay language, grounded in the Paper's own text.
- **Conversational Analytics:** chat grounded in the Corpus to discuss
  Papers, Candidate Gaps, and dashboard data.
- **Gap cards:** one card per Candidate Gap, with its Gap Type, a short written
  explanation, a confidence level, and links to the Evidence.
- **Paper comparison:** the researcher picks 2 or more Papers and sees them
  side by side: research question, methods, population, datasets, findings,
  limitations. Agreements and contradictions are highlighted, and the view
  shows what none of the selected Papers covers (a mini coverage matrix for
  just that selection). Works only if every Extraction uses the same
  normalized categories (pipeline stage 3).
- **Narrative summary:** a few paragraphs a researcher could adapt for a
  "gaps in the literature" section, citing only papers in the Corpus.

## Candidate vocabulary (for `/domain-modeling` to confirm or reject)

All candidate terms (including Gap Type and its v1 taxonomy, Paper Explainer,
and Conversational Analytics) are resolved and now live in `CONTEXT.md`.

## Open decisions (resolve during grilling, record as ADRs)

1. **What counts as a gap (settled):** v1 detects Knowledge Gaps, Coverage
   Gaps, Unanswered Limitations, and Retrieval Gaps; contradictions and
   theoretical gaps are out of scope (see `CONTEXT.md` > Gap Type).
2. **Corpus input (partly settled):** a DOI/BibTeX list plus matching
   full-text PDF uploads, stored in a local multimedia data lake; extraction
   is section-aware over full text. Papers may be added from the dashboard and
   may have no DOI (`docs/adr/0007`, `CONTEXT.md` > Paper). Still open: exact
   storage layout and whether a scholarly API assists ingestion (in-app
   search). Retrieval Gap detection uses OpenAlex (primary) and PubMed, behind
   a source-adapter interface so more APIs can be added easily later.
3. **Extraction method (settled):** LLM-based via Anthropic with disk
   caching; see `docs/adr/0001`. Still open (research task): which Anthropic
   model and which PDF-parsing library — answered in `docs/research/`:
   Claude Sonnet 5 with structured outputs; docling for PDF parsing.
4. **Dashboard framework (settled):** Streamlit over a framework-agnostic
   data artifact; free/open-source tools only; see `docs/adr/0002`.
5. **Human in the loop (settled):** v1 includes accept/reject of Candidate
   Gaps and a Conversational Analytics section grounded in the Corpus.
   Judgments and chat history persist locally as JSON alongside the data
   artifact, so an analysis can run over months on longer projects.
6. **Scale (settled):** 10–75 papers per Corpus in v1; flat files, no
   database.
7. **Text language (settled):** English-only for v1 (papers and dashboard
   text); German and Spanish are candidates for later.
8. **Paper comparison scope (settled, with a v2 feature seed):** 2–5 Papers
   compared directly; a larger selection (up to 15) is split into sets of ≤5,
   each set summarized, and the summaries compared. Single Corpus only in v1.
   Comparing the researcher's own planned or unpublished study against the
   Corpus "does my work close this gap?" is deferred to v2. `docs/adr/0007`
   now *enables* it (no-DOI upload + re-run), but the flow itself is
   unresolved: lean toward a distinct **candidate-contribution** concept that
   holds the draft apart from the Corpus baseline, since an unpublished draft
   is not a published Corpus Paper and must not silently alter denominators or
   be cited as if it were one (research integrity). Needs its own grill.

10. **LLM provider (open, own ADR):** the pipeline's reasoning stages use
   Anthropic (`docs/adr/0001`). Open: support an **open-source/local model**
   (e.g. Llama/Mistral/Qwen via Ollama or an OpenAI-compatible endpoint) as an
   **opt-in** alternative behind a provider seam, with **Anthropic kept as the
   default failsafe**. Benefits: free per-call, private, offline; cost: lower
   quality and setup. A provider-seam decision refining ADR 0001, separate from
   the dashboard boundary (`docs/adr/0007`). Resolve in its own grill.
9. **Scope of v1 (settled):** the medical field, particularly cardiology, is
   the first end-to-end test case.

## Non-negotiables (already decided)

- No invented citations. The dashboard only cites Papers in the Corpus, and
  every Candidate Gap links to its Evidence.
- The dashboard states its own scope and limits: how many papers, which
  sources, which years, and that gaps are candidates for human judgment,
  not verdicts.
