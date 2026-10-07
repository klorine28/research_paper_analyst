# 0010: Vocabulary expansion and the Corpus entry log

## Status

Accepted (supports ADR 0008 and ADR 0009; refines ADR 0007's incremental
re-run)

## Context

An empty Coverage Matrix cell, or a saturation search with no hits, means
something only if the vocabulary behind it is broad enough. A tool can report a
confident gap purely because the field moved to a different term (e.g.
"Asperger's syndrome" now indexed under autism spectrum disorder). Taxonomies
already carry MeSH ids and `aliases` seeded from MeSH entry terms, but many
alias lists are empty, the Method taxonomy is narrow (#56), and Aggregate
surfaces unmapped terms that nobody acts on.

Separately, once Papers can enter a Corpus by several routes (initial import,
Retrieval Gap suggestion, saturation hit, manual upload, later the researcher's
own draft), how the Corpus was built becomes part of its scope. Re-running
after adding a Paper can also regroup limitations: those gaps are keyed by an
LLM-made group id, so a researcher's verdict can be orphaned.

## Decision

1. **Vocabulary sources.** Taxonomy aliases are expanded from:
   - MeSH entry terms and **child descriptors** (free, public-domain NLM data;
     child descriptors also serve ADR 0008's roll-up layer);
   - the Corpus's own **mapped original terms** (already on disk);
   - **promoted unmapped terms** (below);
   - **LLM-suggested aliases**, only for categories with few aliases, run once
     and cached.
   UMLS is not used: its licence conflicts with the free/open-source rule (ADR
   0002).
2. **Human-approved, per-Corpus overlay.** Every new alias is approved by a
   human, logged and reversible. Approved aliases live in a per-Corpus overlay.
   Promoting one to the shared taxonomy file is a deliberate, separate act.
3. **Promotion filter for unmapped terms.** A term is offered only if it came
   from **verified Evidence**, recurs in **at least 2 Papers**, and is not a
   number, unit, dosage, statistic, citation fragment, author name, generic
   word ("patients", "study", "results") or stopword-heavy phrase (a
   deterministic filter with a stoplist). Thresholds are tuned in
   `docs/research/gap-detection-layers.md`.
4. **Corpus entry log.** Every Paper's entry route and date are recorded with
   the Corpus scope. Overview summarises it ("15 imported · 2 from retrieval
   suggestions · 1 from a saturation check") and keeps it as an internal
   timeline of how the Corpus grew. Unavailable papers (ADR 0009) appear there
   too. The log discloses a real bias: a Corpus grown by following the tool's
   own suggestions is not the same as a searched one.
5. **Incremental re-run discipline.** Before ingesting an added Paper, the
   10–75 envelope is checked and refused clearly if exceeded. After every
   re-run, a **"changed since last run"** summary shows gaps closed by new
   Papers, new gaps and orphaned verdicts. **Limitation-gap verdicts are
   re-keyed by their verbatim source statements**, not the LLM group id, so
   regrouping does not orphan them. Cell-gap ids are already stable.

## Consequences

- Fewer false gaps and fewer false "no closing work found" results; Aggregate
  maps more terms, which also helps #56.
- The taxonomy stays a controlled vocabulary: nothing enters without human
  approval, and per-Corpus overlays stop one analysis's choices from leaking
  into another's.
- The scope statement grows from a static description into a dated record of
  how the Corpus was assembled, which the Resume step can build on.
- Judgments gain a second keying scheme for limitation gaps. Existing verdicts
  need a one-off migration to statement-based keys.
