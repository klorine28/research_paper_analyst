# Analysis: AIPOCH medical-research-gap-finder vs. this project

Question: does AIPOCH's open-source gap-finder skill overlap with our tool,
should we install any of their skills, and what should we borrow?

Sources: [aipoch/medical-research-skills](https://github.com/aipoch/medical-research-skills)
(MIT license) — `Evidence Insight/medical-research-gap-finder/SKILL.md` and
its `references/` files; the AIPOCH blog post
"How to Find Research Gaps in Medical Research Using AI Agent Skills".

## What it is

A prompt-driven Claude agent skill (Markdown instructions, no code). Input is
a plain-language topic; the agent retrieves literature live (PubMed primary,
Google Scholar, Web of Science, preprints), maps the evidence landscape, and
emits a one-off `.md` gap report. Eight fixed steps: scope → retrieve →
evidence-landscape audit → candidate gaps → pseudo-gap rejection → confidence
& priority → gap-to-study conversion → self-critical review.

## How it differs from our tool

| | AIPOCH skill | Research Gap Dashboard |
| --- | --- | --- |
| Form | Prompt skill, one-shot report | Product: pipeline + persistent dashboard |
| Corpus | Live retrieval at question time | Curated full-text Corpus (10–75 PDFs) |
| Artifacts | Single `.md` file | Inspectable per-stage artifacts, cache, JSON persistence |
| Evidence | Prose citations in report | Passage-level Evidence links per Candidate Gap |
| Human loop | None | Accept/reject, Conversational Analytics, long-running projects |
| Reproducibility | Depends on agent run | Cached, re-runnable stages |

Not a competitor to the product; it is a well-designed *prompt architecture*
for the same problem.

## Should we install their skills?

**No.** They are one-shot agent workflow prompts, not libraries; our Detect
stage needs deterministic code with LLM calls inside it, not an agent
persona. Our installed `paper-lookup` skill already covers the retrieval
APIs. Nothing in their repo is a runtime dependency.

## What to borrow into our Detect-stage spec (with attribution, MIT)

1. **Pseudo-gap rejection rules** (`pseudo-gap-rejection-rules.md`): five
   pseudo-gap classes (generic upgrade, template reuse, pseudo-firstness,
   non-closable, low-value replication) and a five-question rejection check
   (e.g. "would this statement still sound true for another disease?").
   Directly fills the filtering step our pipeline lacked. Also their output
   requirement: a **"Pseudo-Gaps Rejected"** section — a natural extra
   dashboard element that builds trust.
2. **Confidence rubric**: High/Medium/Low with explicit criteria; only
   medium/high gaps surface prominently. Maps onto our gap-card confidence
   level, which was undefined until now.
3. **Gap table columns**: Gap Statement / Audit Basis (what the literature
   already covers) / Why This Is a Real Gap / Why It Matters / Minimal Study
   That Can Answer It / Confidence. "Audit Basis" and "Minimal Study" are
   strong additions to our gap-card schema.
4. **High-credibility criteria** (4-of-7 test) and prohibited output styles
   ("more validation is needed") as prompt guardrails for gap explanation
   text.
5. **Self-critical review step**: a final LLM pass challenging each gap
   against its Evidence before it enters the artifact.
6. Their **gap taxonomy** (9 types) is finer than ours; our four v1 Gap Types
   stand (CONTEXT.md), but their "consistency gap" ≈ our deferred
   contradiction detection, and "validation gap"/"stage-context gap" are v2
   taxonomy candidates.

## Conclusion

No new agent skills to install. The value is design input: fold items 1–5
into the Detect-stage spec and gap-card schema when `/to-spec` runs, citing
aipoch/medical-research-skills (MIT) in acknowledgments per AGENTS.md.
