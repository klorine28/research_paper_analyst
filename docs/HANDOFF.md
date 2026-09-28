# Handoff notes

A running log of small findings and follow-ups that aren't yet full GitHub
issues — so nothing gets lost between sessions. Promote an entry to a real issue
(`gh issue create`) when it's ready to schedule. Newest first.

---

## Run pipeline commands (detect, etc.) from inside the dashboard

**Found:** while demoing the Gap Cards view (#17). Today the app can only *read*
artifacts. If a corpus was ingested but never had `detect` run, the Gap Cards
page just shows "Run `detect` first" and the researcher has to drop to a
terminal. Same for a corpus that hasn't been through `extract` / `aggregate`
yet, or one where new PDFs were added and the pipeline needs re-running.

**Idea:** add controls to the app that run the relevant pipeline commands for
the selected corpus, so the whole loop (ingest → parse → extract → aggregate →
detect → review) can be driven from the UI. Minimum useful version:

- A **"Run detect"** button on the Gap Cards page when the CandidateGaps
  artifact is missing or stale, plus a way to re-run it after edits.
- Buttons / a "pipeline status" panel for the upstream stages
  (`ingest`, `parse`, `extract`, `aggregate`, `detect`, `taxonomy` — see
  `src/research_gap_dashboard/cli.py`), showing which artifacts exist and which
  are out of date, so the researcher knows what to run next.
- Surface progress and the command's stdout/stderr, and the exit code, rather
  than freezing silently (these stages can be slow and call the LLM).

**Watch out — this touches ADR 0002.** That ADR says the dashboard "only reads
the data artifact and never runs pipeline logic", precisely so the front end can
be swapped. Adding run buttons is a real change to that boundary and needs an
ADR decision first (via `/skill:domain-modeling`), not a quiet break. A design
that likely *keeps* the spirit of ADR 0002:

- The dashboard shells out to the existing CLI as a **subprocess**
  (`uv run research-gap-dashboard <command> <corpus>`) instead of importing any
  pipeline stage — so `test_dashboard_imports_no_pipeline_logic` still holds and
  the front end stays swappable.
- Keep the subprocess/orchestration seam in its own dashboard module (e.g.
  `dashboard/pipeline_runner.py`) with the invocation logic tested directly,
  the way judgments persistence is (mock/patch the subprocess, assert the
  argv and how exit codes/output are reported).

**Also decide:**

- **API key + network.** `extract`, `aggregate`, and `detect` need the
  Anthropic key (ADR 0001) and hit the network. The app must fail clearly when
  the key is missing (CODING_STANDARDS > Configuration) and must never make
  `just test` call a paid API (results are cached; tests patch the runner).
- **Long-running / concurrent runs.** Streamlit reruns on every interaction;
  decide how a run is launched, how re-entry (double-click) is prevented, and
  how a stale artifact is detected (e.g. inputs newer than the artifact).
- **Scope.** This is plausibly its own v1.x issue ("drive the pipeline from the
  dashboard"), parented under #1, once the ADR question is settled.

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
