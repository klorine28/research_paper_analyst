# Research Gap Dashboard

A tool for researchers: point it at a body of literature on a topic and get a
dashboard that shows where the research gaps are, with data, graphs, and short
written explanations, each gap traceable to the papers that support it.

> **Status:** pre-alpha. Scope and design are being settled. See
> [`docs/BRIEF.md`](docs/BRIEF.md) for the draft plan and open decisions.

Built on a `uv`-managed Python template with Matt Pocock's agent skills
pre-installed.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just).

```shell
git clone <your-repo-url> research-gap-dashboard
cd research-gap-dashboard
just init
```

## Configuration

Settings are read from the project-root `.env` (never committed); copy
`.env.example` and fill it in. To change a key, edit that one line in `.env` —
nothing else. Every entry point (the `just` recipes, the `research-gap-dashboard`
CLI, and `streamlit run`) loads `.env` automatically, and a real environment
variable always overrides the file. The LLM boundary (see
[`docs/adr/0001-llm-extraction-with-anthropic.md`](docs/adr/0001-llm-extraction-with-anthropic.md))
reads:

| Variable | Default | Why |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | _(none)_ | Anthropic API key; required for real runs. Leave unset for offline, recorded-response runs. |
| `LLM_DEFAULT_MODEL` | `claude-sonnet-4-5` | Model for the capable (default) tier. |
| `LLM_CHEAP_MODEL` | `claude-haiku-4-5` | Model for the cheap, high-volume tier. |
| `LLM_MAX_TOKENS` | `16384` | Max output tokens per completion; a Paper whose Extraction is cut off at this budget fails with a reason saying so. |
| `OPENALEX_MAILTO` | _(none)_ | Contact email that puts OpenAlex calls in the faster polite pool; `--mailto` overrides it. |
| `NCBI_API_KEY` | _(none)_ | NCBI E-utilities key; raises PubMed's rate limit from 3 to 10 requests a second. |

### Optional embeddings

`uv sync --extra embeddings` installs `sentence-transformers` and enables a
local biomedical embedding model (PubMedBERT, downloaded from Hugging Face on
first use). `detect` then gives limitation grouping the cross-Paper statement
pairs whose embeddings are close as merge hints, and records the model id,
revision, threshold, and every hint in `candidate_gaps.json`. Without the extra,
grouping runs LLM-only and nothing fails.

| Variable | Default | Why |
| --- | --- | --- |
| `EMBEDDING_MODEL` | `NeuML/pubmedbert-base-embeddings` | Embedding model id. |
| `EMBEDDING_REVISION` | `b79526d6ef3645e0df4530322e266f24c829f5ef` | Model revision, pinned so reruns give the same vectors and hints. |
| `LIMITATION_MERGE_THRESHOLD` | `0.65` | Cosine at or above which two limitation statements from different Papers become a merge hint; must be in (0, 1]. |

The test suite never calls the API or the network: it uses recorded responses,
so `just test` runs with no API key.
No test loads or downloads an embedding model: tests replay recorded vectors.

## Working on this project with an agent

This project is built with the [pi](https://pi.dev) coding agent, Matt
Pocock's engineering skills (vendored in `.agents/skills/`), and the
[`pi-scientific-skills`](https://pi.dev/packages/pi-scientific-skills) package.

### One-time setup

```shell
# Install pi (needs Node.js; this is a global tool, not a repo dependency)
npm install -g --ignore-scripts @earendil-works/pi-coding-agent

# Install the scientific skills package (review its source first:
# pi packages can execute code)
pi install npm:pi-scientific-skills
```

Then start `pi` in the repo root, trust the project when asked (needed for
pi to load `.agents/skills/`), and log in with `/login` or an API key.

Inside pi, run:

1. `/sci search`: load only the scientific Core skills; the rest stay
   reachable through search. Loading all 161 costs ~18k tokens of context on
   every turn, and most (genomics, chemistry, ...) don't apply here.
2. `/skill:setup-matt-pocock-skills`: pick your issue tracker (GitHub
   Issues, or local markdown for a solo project) and keep the default
   triage labels.

Pi runs commands without permission prompts. Consider running it in a
container; see pi's [containerization docs](https://pi.dev/docs/latest/containerization).

### The workflow

1. `/skill:grill-with-docs` and point it at `docs/BRIEF.md`. The first
   session resolves the brief's open decisions; as they're resolved, the agent
   writes terms into `CONTEXT.md` and decisions into `docs/adr/`, and removes
   them from the brief.
2. `/skill:to-spec`: turn the agreed conversation into a written spec.
3. `/skill:to-tickets`: split the spec into small, buildable tickets.
4. `/skill:implement`: build a ticket test-first.
5. `/skill:code-review`: review the diff against the standards and the spec.

Not sure which skill fits? `/skill:ask-matt`. Need scientific domain help (a
scholarly API, a statistical method, chart design)? `/sci find <what you're
doing>`. Ending a session mid-task? `/skill:handoff`. Pi sessions are trees:
`/tree` lets you go back and branch if a design path goes wrong.

## Project docs

| File | Audience | Purpose |
| --- | --- | --- |
| `README.md` | Humans | What the project is and how to run it |
| `docs/BRIEF.md` | Humans + agents | Draft plan and open decisions; shrinks over time |
| `docs/corpus-layout.md` | Humans + agents | The Corpus directory convention every stage relies on |
| `docs/taxonomy.md` | Humans + agents | The MeSH-seeded Topic, Method, Population, and Dataset taxonomies and how to edit them |
| `CONTEXT.md` | Agents | Resolved domain vocabulary (created during grilling) |
| `docs/adr/` | Humans + agents | Recorded design decisions (created during grilling) |
| `AGENTS.md` | Agents | Environment, commands, and skill conventions |
| `CODING_STANDARDS.md` | Humans + agents | How code in this repo is written |

## Commands

| Command | What it does |
| --- | --- |
| `just init` | Create the virtualenv and install dependencies |
| `just test` | Run the test suite |
| `just lint` | Ruff + pylint |
| `just format` / `just format-check` | Format / check formatting |
| `just type-check` | Pyright + ty |
| `just dashboard` | Launch the Streamlit dashboard over ingested corpora |

## Dashboard

The dashboard is a Streamlit app that reads the on-disk artifacts the
pipeline writes; it never imports pipeline logic (see `docs/adr/0002-...`), but
it may launch the CLI as a subprocess (`docs/adr/0007-...`). Ingest
a corpus first, then launch the app:

```shell
uv run research-gap-dashboard ingest path/to/corpus
just dashboard
```

Set `RESEARCH_GAP_CORPORA_DIR` to the folder that holds your corpus directories
(defaults to `./corpora`); the sidebar lists every corpus under it that has a
manifest. The dashboard opens on a Home screen with the Corpus's health and a
recommended next step; the sidebar groups the pages under the numbered steps of
the guided path (1 Scope → 2 Build → 3 Trust → 4 Explore → 5 Judge → 6 Grow → 7
Write), and every page has a step bar and Previous/Next buttons, while any page
stays one click away. The Corpus Overview page shows the paper count, papers per year,
venues, and what was left out, alongside a prominent reminder that detected
gaps are candidates for human judgment, not verdicts.

The Gap Cards page shows one card per
Candidate Gap: its Gap Type, explanation, confidence, cell count, and the
verbatim Evidence passages with their source Papers. Accept or reject each
card; judgments persist to JSON under the corpus's `judgments/` directory and
are restored when you reopen the corpus, so a review can span months. Before
`detect` has run, the page offers a **Run detect** button that runs
`uv run research-gap-dashboard detect <corpus>` as a blocking subprocess (the
app must be launched from the project directory). It refuses to start without
`ANTHROPIC_API_KEY`, refuses a second run while one is in progress on the same
corpus (a lock under `judgments/`), and reports the run as succeeded, completed
but incomplete (exit code 2), or failed (exit code 1), with stdout and stderr
under **Run output**.

The Pipeline page lists every stage (`ingest`, `parse`, `extract`, `explain`,
`aggregate`, `detect`, `retrieve`) as **done**, **not run**, or **stale**, read
only from which artifacts exist and when they were written: a stage is stale
when one of its inputs (say, a PDF added after `ingest`) is newer than its
artifact, and the page names the changed inputs. Every stage has a **Run** or
**Re-run** button, always available, that launches the stage the same way. Cost
stays out of the way in a collapsed **Run details** expander: each LLM stage's
estimated cost (a rough upper bound — Papers × a per-Paper token allowance at
the default model's list price, as if nothing were cached) and the actual cost
of its last run, priced from the token usage the CLI records in
`artifacts/llm-usage.json`. A run whose estimate is above
`LLM_COST_CONFIRM_ABOVE` asks for confirmation before it starts; a model with
no known price always asks.

| Variable | Default | Why |
| --- | --- | --- |
| `LLM_COST_CONFIRM_ABOVE` | `1.00` | US dollars. A dashboard run estimated above this asks "Run anyway?" first, so an expensive run never surprises you. `0` confirms every LLM run; a value that isn't a non-negative number is reported in the app and treated as `0`. |

Every other page's empty or guard state works the same way: instead of naming
a command, it offers the button that runs the missing stage (e.g. **Run
extract** on Verify Extractions, **Run retrieve** on Retrieval Gaps, **Run
ingest with DOI resolution** on the citation and author graphs). With no
ingested corpus at all, the app offers **Run ingest** for each folder under
`RESEARCH_GAP_CORPORA_DIR` that holds a `papers/` directory.

Papers are added in the app too. A Corpus created with **New analysis** opens
on an **Add Papers** panel (also on the Pipeline page, collapsed, for adding to
an ingested Corpus): upload the PDFs and a paper list (`.bib` or a `.txt` DOI
list). PDFs land in `papers/`; the paper list becomes `corpus.bib`/`dois.txt` or
is appended to the existing one, skipping entries already listed. Straight
after an upload the app runs `ingest --check`, which pairs the paper list with
the PDFs exactly as ingest would but writes nothing, and shows the unmatched
entries and orphan PDFs to fix. **Ingest the Corpus** checks again and runs
`ingest` only when the Corpus is inside the 10–75 Paper envelope; outside it,
ingest is refused with the count. The manifest is still written only by
`ingest`.

The Verify Extractions page (available once `extract` and `parse` have run) lets
a researcher check each Paper's Extraction against its own text, one field at a
time, and leave per-field verdicts: approve, edit, flag wrong, remove a
hallucinated fact, or add a missing one. Verdicts are annotation-only (see
`docs/adr/0003-...`): they persist to a review overlay under `judgments/` and
never change `extractions.json`, so `aggregate`/`detect` stay deterministic. Any
edited or added Evidence must be a verbatim quote from the Paper's parsed text,
re-verified before it is saved. **Promote to Gold Extraction** on that page
turns the reviewed Extraction into a committed Gold Extraction regression
fixture by running the pipeline's `promote-gold` command through the runner
(the browser never writes the fixture itself). Promotion is strict: if any kept
quote is not verbatim in its Paper, nothing is written and the page shows which
Paper and passage refused it. The same step from a terminal:

```shell
uv run research-gap-dashboard promote-gold path/to/corpus
```

On the Needs Attention page, pasting a broken Paper's correct text and clicking
**Apply correction & re-extract** saves the text to a `judgments/` overlay, then
runs `apply-parse-corrections` and `extract` in one locked run and redraws the
queue (ADR 0006/0007).
