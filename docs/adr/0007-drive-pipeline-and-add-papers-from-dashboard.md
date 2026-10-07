# 0007: Driving the pipeline and adding Papers from the dashboard

## Status

Accepted (refines ADR 0002; builds on ADR 0003, ADR 0006)

## Context

The dashboard can only *read* artifacts (ADR 0002). Everything that changes a
Corpus — running a stage, adding a Paper, applying a correction — requires a
terminal. A UI audit (`docs/research/ui-audit-findings.md`) found this is the
single most pervasive usability defect: nine pages degrade to a "run `X` first"
message that dumps the researcher to a CLI they are, by persona, not expected to
use. The product goal is that a researcher can run a whole analysis — create a
Corpus, add Papers, run the pipeline, fix fallout, re-run — **without CLI
knowledge**.

ADR 0002 says the dashboard "only reads that artifact and never runs pipeline
logic," so the front end stays swappable; `test_dashboard_imports_no_pipeline_logic`
enforces it. ADR 0003 and 0006 refined the boundary once already: the dashboard
writes only researcher-review *overlays* under `judgments/` and never pipeline
artifacts, and corrections round-trip to the pipeline across a seam applied by a
pipeline command (`apply-parse-corrections`, `promote-gold`). The open question
was whether the dashboard may *invoke* the pipeline at all, and whether it may
write the Corpus's *inputs* (`papers/`, the paper-list).

## Decision

The dashboard may drive the pipeline and add Papers, within a refined boundary:
**it still imports no pipeline logic and writes no pipeline artifact itself; it
may invoke the pipeline as an external process, and may place a Corpus's inputs
on disk.**

1. **Invoke, don't import.** A dedicated `dashboard/pipeline_runner.py` shells
   out to the existing CLI as a subprocess
   (`uv run research-gap-dashboard <command> <corpus>`). No dashboard module
   imports a pipeline stage, so `test_dashboard_imports_no_pipeline_logic` still
   holds and the front end stays swappable. The runner is the one seam; stage
   logic stays in the pipeline.

2. **Seam commands too.** The runner also invokes the existing seam commands
   (`apply-parse-corrections` then re-`extract`, `promote-gold`), so the
   overlay-write / pipeline-apply split of ADR 0003/0006 is preserved unchanged
   — the *apply* simply happens through the runner instead of a human typing it.
   This is what closes the "fixing anything sends you back to the terminal"
   gap.

3. **Blocking run, guarded by a lock.** A run is a blocking subprocess with a
   spinner; a per-Corpus run-lock prevents a double-click or a second tab from
   launching a concurrent run (Streamlit reruns the script on every
   interaction). stdout/stderr and the exit code are surfaced on completion,
   and `EXIT_INCOMPLETE` (code 2, ADR 0006) is reported as "completed but
   incomplete," distinct from code 1 ("failed to run"). Live-streamed
   background runs are a later enhancement, not part of this decision.

4. **Status from existence + mtime.** The pipeline-status panel marks each
   stage done / not-run / stale, where *stale* means an input is newer than the
   artifact it produced. A re-run is always available regardless of the status;
   the panel guides, it never blocks. The pipeline is already cache-keyed by its
   inputs, so a needless re-run is cheap and reproducible.

5. **Inputs may be placed; artifacts may not.** Adding Papers writes the
   uploaded PDFs into `papers/` and appends to the paper-list
   (`.bib`/`dois.txt`), and writes the scope statement captured at Corpus
   creation. These are the Corpus's *inputs*, not pipeline artifacts, and
   placing a file is not pipeline logic. The manifest — an artifact — is still
   produced only by `ingest`, run through the subprocess runner.

6. **Key and test isolation.** The runner checks for the required credential
   (e.g. `ANTHROPIC_API_KEY`) *before* launching any stage that needs it, and
   shows a clear in-app message instead of launching when it is missing
   (CODING_STANDARDS > Configuration). `just test` never invokes the real
   runner: tests patch `pipeline_runner` and assert the argv and exit-code
   handling, exactly as `judgments`/overlay persistence is tested. Nothing in
   the suite calls a paid API or the network.

## Consequences

- ADR 0002's intent holds, its letter is refined: the dashboard runs no
  pipeline logic in-process and writes no artifact; it invokes an external
  process and places inputs. The swappable-front-end property survives because
  the seam is a subprocess call to a CLI, not a code dependency.
- The pervasive "run `X` in a terminal" messages (UI audit H9) can be retired
  for run buttons and a status panel; the whole loop becomes CLI-free.
- The subprocess seam is the one place to test: mock it and assert the argv,
  the lock behaviour, exit-code mapping, and the key pre-check.
- Streamlit's rerun model makes the run-lock load-bearing; without it a second
  interaction could launch a concurrent, Corpus-corrupting run.
- A no-DOI Paper is now ingestable (see `CONTEXT.md` > Paper); upload must not
  assume a DOI exists.

## Not in this decision (tracked follow-ups)

- **LLM provider choice.** Supporting an open-source/local model with Anthropic
  as the default failsafe is a provider-seam decision refining ADR 0001, not a
  boundary decision; see `docs/BRIEF.md` open decisions.
- **Checking one's own unpublished work against a gap** (the "candidate
  contribution" flow) is enabled by no-DOI upload here but is a separate feature
  with its own integrity questions; see `docs/BRIEF.md` item 8.
- **Pipeline improvements** beyond the seam are out of scope here.

## Implementation slices (issues, not part of the decision)

1. `pipeline_runner.py` subprocess seam: lock, key pre-check, exit-code mapping,
   and the stage-status panel (existence + mtime).
2. In-app upload: place PDFs + paper-list, capture the scope statement, run
   `ingest`; support no-DOI Papers.
3. Wire per-page run / re-run buttons and the seam-command buttons (apply
   correction & re-extract, promote gold), retiring the H9 terminal messages.
