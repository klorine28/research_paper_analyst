# 0011: The Method axis is study design; procedures and treatments are Topics

## Status

Accepted (resolves #56; from "Scope the pipeline improvements", #64)

## Context

The Extraction's `methods` field mixes study designs ("retrospective cohort")
with clinical procedures (echocardiography, coronary angiography), treatments,
lab assays and statistics. The Method taxonomy holds only study designs, so on
`takotsubo-15` just 4 of 53 method facts mapped and 49 were set aside as
unmapped. Papers also often state their design only implicitly. The result is a
nearly empty Method axis and Topic × Method Coverage Gaps that say nothing.

## Decision

1. **Method means study design only**: how the study was built (randomized
   trial, cohort, case report, registry analysis…), never what was done to
   patients.
2. **Procedures and treatments are Topics.** Aggregate already maps every
   Extraction field onto the Topic axis. The default cardiology Topic seed gains
   a curated set of diagnostic and therapeutic procedures from MeSH's procedure
   branches (E01/E02/E04), so this works beyond hand-made taxonomies.
3. **Lab assays and statistical techniques are on no axis.** On the Method axis,
   non-design terms are recorded as "not a study design", not as unmapped, so
   they never become Method alias suggestions (ADR 0010).
4. **Every Paper states its design plainly.** Extraction asks for one explicit
   study-design fact per Paper, in everyday words ("retrospective cohort study
   using registry data"), quoted from the Paper like any other fact. The
   dashboard shows it in the same plain words.

## Considered options

- **A fifth "Intervention/Procedure" axis.** Rejected: one more matrix with
  hundreds of extra candidate cells, and detection, dashboard and docs all
  assume four axes.
- **Broaden Method to include procedures.** Rejected: it mixes "how the study
  was designed" with "what was done to patients", which makes Topic × Method
  gaps unreadable.

## Consequences

- The extraction prompt version is bumped, so existing Corpora must be
  re-extracted once (done together with the test-corpus refresh).
- Topic × Method Coverage Gaps become meaningful ("no prospective cohort on
  X"), and procedure gaps appear as Knowledge Gaps (Topic × Topic).
- The researcher can still prune the procedure categories from the seed.
