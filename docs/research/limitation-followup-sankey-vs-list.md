# Validation: Limitation follow-up as a Sankey vs. the list view

**Question this note answers.** Issue #49 graph #5 asks us to *"prototype as
Sankey/matrix and confirm it beats the existing list view before committing to
node-link."* This note records that validation.

**Scope caveat.** Everything below is computed only over the Papers in the named
Corpus. "Addressed" means a later Paper *in the Corpus* addressed the
limitation; it never claims the wider literature left it open.

## What each view is for

| | Unanswered Limitations page (list) | Field Meta-Analysis Sankey |
|---|---|---|
| Unit | one card per limitation group | one flow per group into two sinks |
| Shows | verbatim source + follow-up **Evidence**, per group | the **addressed / still-open split** at a glance |
| Reads | top-to-bottom, one group at a time | whole-Corpus ratio and magnitude in one look |
| Encodes magnitude | as text ("3 follow-ups") | as **edge width** (3× a one-follow-up flow) |

The meta-analysis layer's job is the *field-level overview*: "of everything this
Corpus flagged, how much got taken up and how much is still open?" That is a
ratio-and-magnitude question, which is exactly what a two-column Sankey answers.

## The test

The real corpora do not exercise a mixed split — on both `takotsubo-15`
(14 groups) and `cardiology` (3 groups) **every** group is still open, 0
addressed. In that degenerate case the Sankey fans every group into one sink and
says the same thing as the list's summary line ("14 of 14 still open"): a **tie**,
not a win. So the honest test needs a mixed Corpus, built synthetically:

```
Small samples        -> Addressed by a later Paper   width=3
Single-centre        -> Addressed by a later Paper   width=1
No long-term data    -> Still open                   width=1
No control arm       -> Still open                   width=1
Retrospective only   -> Addressed by a later Paper   width=2
                       addressed=3   open=2
```

- **List view:** the reader scrolls five cards and mentally tallies 3 addressed
  vs 2 open, and separately notices "Small samples" drew the most follow-up.
- **Sankey view:** the 3-vs-2 split and the heavier "Small samples" flow are
  visible in one glance, before reading a single card.

## Conclusion — the Sankey earns its place, and does not replace the list

- On the **overview question** (addressed/open ratio and magnitude), the Sankey
  **beats** the list whenever the Corpus has a mix and the follow-up counts vary;
  it **ties** the list when every group shares one outcome; it never **loses**.
- On the **Evidence question** (what exactly was the limitation, and what later
  Paper addressed it, in the Paper's own words), the **list wins** — the Sankey
  deliberately carries none of that verbatim text.

Decision: **keep both, as overview and detail.** The Sankey ships in the
Field Meta-Analysis layer as the at-a-glance split; the Unanswered Limitations
page remains the Evidence-bearing detail view one click away. This satisfies
"confirm it beats the list before committing": it beats the list at the job the
meta-analysis layer exists to do, without pretending to replace the Evidence
list. A matrix was not pursued: with only two outcomes (addressed / open) a
matrix collapses to a 2-cell table that the summary line already states.
