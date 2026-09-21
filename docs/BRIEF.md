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
- **Contradictions:** findings that disagree, side by side with their sources.
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

- **Corpus:** the set of papers one analysis runs over.
- **Paper:** one publication in a Corpus, identified by DOI where available.
- **Extraction:** the structured facts pulled from one Paper.
- **Evidence:** a specific passage or data point in a Paper that supports a
  claim the dashboard makes.
- **Candidate Gap:** something the tool proposes as a gap, before a human
  confirms it.
- **Gap Type:** the kind of gap. A common starting taxonomy: evidence gap
  (contradictory findings), knowledge gap (not studied at all), methodological
  gap, empirical gap (claims not yet tested), theoretical gap, population gap.
- **Coverage Matrix:** a two-axis count of Papers per category pair.

## Open decisions (resolve during grilling, record as ADRs)

1. **What exactly counts as a gap?** Which Gap Types does v1 detect, and which
   are out of scope?
2. **Corpus input:** user-uploaded PDFs, a DOI/BibTeX list, a live search
   against a scholarly API (OpenAlex, Semantic Scholar, arXiv, PubMed), or a
   combination? Full text or abstracts only?
3. **Extraction method:** LLM-based, rule/NLP-based, or hybrid? Which model and
   provider? How are LLM results cached so reruns are cheap and repeatable?
4. **Dashboard language and framework:** Python (Streamlit, Dash, Shiny for
   Python), R (Shiny), TypeScript (React + a charting library), or a
   generated static HTML report. Leading option: keep the pipeline in Python
   and have it write a clean data artifact (papers, extractions, gaps,
   evidence) that the dashboard only reads, so the front end can be swapped
   later. Choosing R or TypeScript for the front end means a two-language repo
   and changes to `AGENTS.md`, which currently forbids npm/TypeScript tooling.
5. **Human in the loop:** can the researcher accept, reject, or edit Candidate
   Gaps, and should those edits persist?
6. **Scale:** target corpus size for v1 (e.g. 50 papers vs. 5,000). Drives
   cost, storage, and whether a database is needed.
7. **Text language:** is dashboard text in English, Spanish, or switchable? Are
   non-English papers in scope?
8. **Paper comparison scope:** how many Papers can be compared at once? Only
   Papers inside one Corpus, or also across Corpora? Should a researcher be
   able to compare their own planned study (a draft abstract or research
   question) against the Corpus to see whether it fills a Candidate Gap?
9. **Scope of v1:** which single field or example topic is the first
   end-to-end test case?

## Non-negotiables (already decided)

- No invented citations. The dashboard only cites Papers in the Corpus, and
  every Candidate Gap links to its Evidence.
- The dashboard states its own scope and limits: how many papers, which
  sources, which years, and that gaps are candidates for human judgment,
  not verdicts.
