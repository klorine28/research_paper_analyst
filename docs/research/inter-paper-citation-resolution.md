# Research: inter-paper citation resolution

Resolves GitHub issue #51 (child of the v2 epic #44). This is a **design-before-
build** note: it scopes _how_ to resolve the citations that appear inside a
Paper's Evidence passages ("[12]", "(Smith et al., 2019)") to another **Paper in
the Corpus**, and _how to keep that honest_, before any code is written. No
pipeline code ships from this issue.

## The question

When the dashboard shows an Evidence passage, that passage often contains an
inline citation marker. Two features want to turn that marker into a link:

- the Evidence "what it references" pointer (deferred from #47), and
- the citation-network graph (#49), whose in-Corpus edges today come only from
  OpenAlex `referenced_works`.

The task: given an inline marker in a passage, decide **which Corpus Paper (if
any) it points to**, with a verification story strong enough that we never show a
wrong or invented link. Inline-citation resolution is the **highest-hallucination-
risk** of the deferred items, so the verification design is the deliverable, not
an afterthought.

## Sources

Primary sources used, all first-party:

- The project's own scholarly-source seam: `src/research_gap_dashboard/sources.py`
  (`OpenAlexAdapter`, `WorkRecord.referenced_works`, `fetch_work`,
  `OPENALEX_BASE_URL = "https://api.openalex.org"`, the `select` field list).
- The Retrieval-Gap detector `src/research_gap_dashboard/retrieval.py`, which
  already ranks and resolves `referenced_works` by citation overlap.
- The parsing seam `src/research_gap_dashboard/parsing.py` (section detection;
  the Evidence passages come from parsed section text).
- The prior PDF-parsing assessment `docs/research/pdf-parsing-library.md`
  (GROBID's role and its deployment cost).
- `CODING_STANDARDS.md` > Research integrity (never invent citations; every claim
  traceable) and `CONTEXT.md` (Evidence, Corpus, Paper).

## What the Corpus already gives us for free

The pipeline already ingests, per Paper, the OpenAlex `referenced_works` list and
`cited_by_count` (`sources.py`), and the citation-network graph already draws
**in-Corpus edges** from `referenced_works` (#49). That means we already know, at
the _work_ level, which Corpus Papers a given Paper cites — **without parsing a
single reference string**. The gap this issue addresses is narrower and harder:
mapping a _specific inline marker inside a specific passage_ to one of those
cited works, so the link is anchored to the sentence the reader is looking at.

This reframes the problem into two layers of increasing risk:

1. **Passage → cited-work set (low risk).** The target of any marker in Paper P
   must be one of P's `referenced_works` (already known, already DOI-resolvable
   via `fetch_work`). This set is the allow-list, exactly like the citation
   allow-list in Conversational Analytics (`analytics.py`).
2. **Marker → the _one_ cited work it denotes (high risk).** This is the step
   that can hallucinate, because it has to interpret the marker.

## Why a references-section parser is needed (and GROBID's role)

The two marker styles need different machinery, and only one needs new parsing:

- **Numeric markers** ("[12]") are indices into the Paper's own numbered
  reference list. Resolving them requires parsing the **references section** into
  an ordered list, then taking entry _12_. The project does not build a
  references-section parser today; `parsing.py` detects body sections, not a
  structured, ordered bibliography. This is **GROBID territory**:
  `docs/research/pdf-parsing-library.md` already identifies GROBID as the
  reference tool for scholarly PDF → TEI-XML with a dedicated, ordered
  `<listBibl>` of parsed references, and already records its cost (a Java/Docker
  service, which is why docling, not GROBID, parses bodies today).
- **Author-year markers** ("(Smith et al., 2019)") carry the surname + year
  inline, so they can be matched against the **metadata of the cited works we
  already resolved** (author surnames + `publication_year` from each
  `referenced_works` entry via `fetch_work`) **without** a references-section
  parser at all.

So the author-year path is buildable on today's data; the numeric path is gated
on adding a references-section parser (GROBID or an equivalent ordered-bibliography
extractor).

## Proposed resolution design

A deterministic, allow-list-bounded resolver, per marker, per Paper P:

1. **Candidate set.** Resolve P's `referenced_works` to `WorkRecord`s
   (`fetch_work`, cached per ADR 0001). Keep only those whose DOI also resolves
   to a Paper in the Corpus (reuse the Retrieval-Gap DOI-normalisation in
   `retrieval.py`). This is the allow-list; a marker that cannot land here is
   dropped, never guessed.
2. **Author-year markers.** Parse surname(s) + year from the marker with a
   conservative regex. Match against candidate `WorkRecord`s on (first-author
   surname, `publication_year`). Accept **only on an unambiguous single match**;
   on zero or multiple matches, resolve nothing and show the plain marker.
3. **Numeric markers (gated on the references parser).** Build P's ordered
   reference list from GROBID's `<listBibl>`. Map marker _n_ → the _n_-th entry →
   its DOI → a Corpus Paper. Accept only when the entry's DOI resolves into the
   allow-list.
4. **Output.** A resolved link records: the source passage, the marker verbatim,
   the target Corpus Paper's citation key, and the resolution path
   ("author-year match" / "reference-list index _n_"). The path string is the
   explanation shown to the researcher.

## Verification: how we stay honest

The integrity posture mirrors the one already adopted for generated citations
(`analytics.py` drops any citation not in the Corpus allow-list):

- **Allow-list first.** A marker can only ever resolve to one of P's own
  `referenced_works` that is in the Corpus. Nothing outside that set is linkable.
- **No-LLM core.** The matching is deterministic (regex + exact author-year /
  index lookup). An LLM is _not_ in the resolution path, so there is no free-text
  generation to hallucinate a DOI. (If an LLM is ever used to _segment_ ambiguous
  markers, its output must still pass through the allow-list, and it may never
  emit a DOI.)
- **Abstain by default.** Ambiguity (0 or >1 candidate) resolves to _nothing_.
  The plain marker is shown; a wrong link is worse than no link.
- **Explainable edge.** Every link carries its resolution path and the verbatim
  marker, so a reviewer can check it against the passage.
- **Regression fixtures.** Promote a small set of hand-verified
  (passage, marker, target) triples into committed fixtures, like Gold
  Extraction, so later changes are checked against what a human confirmed. Report
  precision/recall against them; this feature is tuned for **precision over
  recall**.

## Recommendation and scope for implementation

- **Resolve this research task as: build in two phases, author-year first.**
- **Phase 1 (no new dependency):** author-year marker resolution over the
  existing `referenced_works` metadata. Delivers the Evidence "what it
  references" pointer for author-year journals and enriches citation-network
  edges with passage-level provenance. Smallest surface, lowest risk.
- **Phase 2 (adds GROBID or an ordered-bibliography parser):** numeric marker
  resolution. Scope the GROBID service cost (see `pdf-parsing-library.md`)
  against the fraction of the real `cardiology`/`takotsubo` corpora that use
  numeric citations before committing; if numeric-citation Papers are rare, Phase
  2 may not be worth the Java/Docker dependency.
- **Non-goal:** resolving citations to works _outside_ the Corpus. That is the
  Retrieval-Gap feature's job and is already handled separately.

Open implementation tickets should be filed per phase, each with its fixture set
and a precision target, when the team decides to build.
