# Field Meta-Analysis graph audit

An audit of the six meta-analysis graphs (issue #49) against the three real
corpora (`cardiology`, `takotsubo-15`, `takotsubo-50`), asking not "does it
render" but "is it useful, and what does a human need to use it." This note
records the findings and what was changed; a few findings are **upstream data
realities** that the rendering layer can only explain, not fix.

## What was changed (P1, P2/table, P4)

- **Readable citation labels.** Nodes now read `Templin 2015`, not `templin2015`;
  the full title and key stay in the hover.
- **Co-occurrence filters.** A *minimum Papers per link* slider hides weak
  single-Paper links (63% of topic edges in `takotsubo-15`), and a *maximum
  categories* slider caps the node count. Both report what they hide, so the
  denominator still travels with the chart.
- **Ranked missing pairs.** Absent edges are ranked by the weaker endpoint's
  coverage (two well-studied categories never combined = the strongest gap),
  trimmed to the top 15 with a "+N more" pointer, instead of a raw dump of 106.
- **Author graph → table.** The co-authorship node-link was an unreadable
  hairball (`takotsubo-50`: 291 nodes, 3066 edges, one author touching 246
  others) and carries no gap signal. It is now a ranked table (author, Papers,
  distinct co-authors), which answers the same field-meta question legibly.
- **Actionability + honesty.** A selected citation node can open its Paper in the
  Explainer; missing-edge rows jump to Gap Cards; each figure carries a text
  summary (accessibility) next to its data-table fallback; empty/degenerate
  states now explain themselves inline.

## Upstream realities the layer can only explain (P3)

These are **not rendering bugs**; the fix is in the pipeline, and the graphs now
say so inline rather than showing a bare degenerate picture.

### Graph 5 (Limitation follow-up) is all-open on every real corpus

`cardiology` (3 groups) and `takotsubo-15` (14 groups) both show **0 addressed**.
The cause differs by Corpus:

- **cardiology:** every group's source Paper is from 2021 and there is **no later
  Paper in the Corpus to check** (`later_checked = 0` for all three). The
  addressed/open question is unanswerable here, not answered "no".
- **takotsubo-15:** groups *were* checked against later Papers (`later_checked`
  up to 9), and the follow-up LLM found **no follow-ups**. Also every group is a
  **singleton** (`sources = 1`): the grouping step is not merging limitations
  that mean the same thing.

The Sankey now flags the all-open case and reports how many groups had no later
Paper to check. The singleton-grouping and follow-up recall are pipeline
concerns (detect / limitations), tracked separately.

### Graph 6 (Method co-occurrence) is thin because the taxonomy is narrow

`takotsubo-15` extracted **52 method facts** but normalization mapped only **4**
to Method categories and set aside **55 as unmapped** — they are clinical
procedures and treatments (intubation, CT, antibiotics), not research study
designs, and the Method taxonomy is study-design-only. A thin method graph here
is upstream taxonomy coverage, not a real Coverage Gap; the graph now says so and
points at taxonomy editing.

## Still open (candidate follow-ups, not done here)

- Merge synonymous limitations so groups stop being singletons (detect).
- Investigate follow-up matcher recall (0 found where later Papers exist).
- Consider broadening the Method taxonomy, or a second "clinical procedure" axis,
  so method co-occurrence has more to say.
- Deep-link a missing-edge jump to the *specific* Knowledge/Coverage Gap card,
  not just the Gap Cards page (needs a card anchor/filter).
- Static figure export + alt text (Kaleido/Chrome not installed here).
