# CONTEXT

Glossary of canonical terms for the Research Gap Dashboard. Terms here are
resolved; draft terms live in `docs/BRIEF.md` until a grilling session
promotes them.

## Terms

### Corpus

The set of Papers one analysis runs over. A Corpus holds between 10 and 75
Papers in v1. Every claim the dashboard makes cites only Papers in the Corpus.

### Paper

One publication in a Corpus, identified by DOI where available, with both its
bibliographic record (from a DOI/BibTeX list) and its full-text PDF.

### Extraction

The structured facts pulled from one Paper: research question, methods,
populations/samples, datasets, key findings, stated limitations, stated
future work.

### Normalized Facts

One Paper's free-text Extraction phrases mapped onto the shared taxonomy
categories of each Coverage Matrix axis (Topic, Method, Population, Dataset),
produced by the Aggregate stage so Papers can be compared in the Coverage
Matrix. Every mapping records its axis, the Paper's original term, the
assigned category, and the Evidence of the extracted fact it came from; a
phrase that fits no category is surfaced as an unmapped term for taxonomy
editing, never silently dropped.

### Evidence

A specific passage or data point in a Paper that supports a claim the
dashboard makes. Every Candidate Gap links to its Evidence.

### Candidate Gap

Something the tool proposes as a research gap, before a human confirms it.
Gaps are candidates for human judgment, not verdicts.

### Gap Type

The kind of gap a Candidate Gap is. v1 detects four Gap Types:

- **Knowledge Gap:** a topic combination no Paper in the Corpus has studied
  (an empty or sparse Coverage Matrix cell).
- **Coverage Gap:** a method or population combination missing from the
  Corpus (e.g. no RCTs in patients over 75). Descriptive only: v1 never
  appraises the quality of an individual Paper's methodology.
- **Unanswered Limitation:** a limitation or future-work statement in a Paper
  that no later Paper in the Corpus addressed.
- **Retrieval Gap:** a Paper that likely exists in the wider literature but is
  missing from the Corpus, found via scholarly-API citation/similarity
  search. The only Gap Type that looks outside the Corpus, and it is labeled
  as such.

Evidence gaps (contradictions) and theoretical gaps are out of scope for v1.

### Paper Explainer

A dashboard section that explains a Paper's experiment twice: once in domain
language and once in lay language. Grounded in the Paper's own text.

### Conversational Analytics

A dashboard section where the researcher discusses the Papers, Candidate
Gaps, and dashboard data in a chat grounded in the Corpus. Its answers cite
only Papers in the Corpus and their Evidence.

### Coverage Matrix

A two-axis count of Papers per category pair (e.g. Topic × Method), used to
surface empty or sparse cells as gap signals.
