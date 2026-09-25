# Handoff notes

A running log of small findings and follow-ups that aren't yet full GitHub
issues — so nothing gets lost between sessions. Promote an entry to a real issue
(`gh issue create`) when it's ready to schedule. Newest first.

---

## Evidence verification is too strict for real PDFs

**Found:** during the [08] extraction verification gate (#9), running `extract`
on real Takotsubo PDFs. Fixes for the bigger bugs landed in #25 / PR #26.

**Problem:** `extract_paper` hard-rejects a *whole paper* on any single Evidence
passage that isn't a verbatim substring of the parsed text. On real PDFs, docling
introduces small artifacts the model "helpfully" normalises when quoting, so a
handful of quotes fail exact matching even though the substance is correct:

- hyphenation/spacing: `in- hospital`, `low- , medium- and high- risk`
- dropped content: a URL rendered as `. . . . .`
- multi-column text linearised out of order

On the 10-paper test corpus this would falsely reject ~4 of 10 papers, despite
their extractions being good (216 facts, ~96% of quotes verify verbatim).

**Idea:** make verification fuzzier instead of exact-substring — e.g. normalise
hyphen/space runs, compare on alphanumerics only, or accept a high token-overlap
match — and/or drop the failing *fact* rather than the whole *paper*. The
throwaway `scripts/verify_extraction.py` already uses soft (per-fact) marking and
is a good reference for what "acceptable" looks like.

**Not urgent** but blocks trusting the real pipeline's output on real corpora.
