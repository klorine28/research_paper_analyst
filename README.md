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
`.env.example` and fill it in. The LLM boundary (see
[`docs/adr/0001-llm-extraction-with-anthropic.md`](docs/adr/0001-llm-extraction-with-anthropic.md))
reads:

| Variable | Default | Why |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | _(none)_ | Anthropic API key; required for real runs. Leave unset for offline, recorded-response runs. |
| `LLM_DEFAULT_MODEL` | `claude-sonnet-4-5` | Model for the capable (default) tier. |
| `LLM_CHEAP_MODEL` | `claude-haiku-4-5` | Model for the cheap, high-volume tier. |
| `LLM_MAX_TOKENS` | `4096` | Max output tokens per completion. |

The test suite never calls the API or the network: it uses recorded responses,
so `just test` runs with no API key.

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

The dashboard is a Streamlit app that only reads the on-disk artifacts the
pipeline writes; it never runs pipeline logic (see `docs/adr/0002-...`). Ingest
a corpus first, then launch the app:

```shell
uv run research-gap-dashboard ingest path/to/corpus
just dashboard
```

Set `RESEARCH_GAP_CORPORA_DIR` to the folder that holds your corpus directories
(defaults to `./corpora`); the sidebar lists every corpus under it that has a
manifest. The Corpus Overview page shows the paper count, papers per year,
venues, and what was left out, alongside a prominent reminder that detected
gaps are candidates for human judgment, not verdicts.

The Gap Cards page (available once `detect` has run) shows one card per
Candidate Gap: its Gap Type, explanation, confidence, cell count, and the
verbatim Evidence passages with their source Papers. Accept or reject each
card; judgments persist to JSON under the corpus's `judgments/` directory and
are restored when you reopen the corpus, so a review can span months.

The Verify Extractions page (available once `extract` and `parse` have run) lets
a researcher check each Paper's Extraction against its own text, one field at a
time, and leave per-field verdicts: approve, edit, flag wrong, remove a
hallucinated fact, or add a missing one. Verdicts are annotation-only (see
`docs/adr/0003-...`): they persist to a review overlay under `judgments/` and
never change `extractions.json`, so `aggregate`/`detect` stay deterministic. Any
edited or added Evidence must be a verbatim quote from the Paper's parsed text,
re-verified before it is saved. Promote a reviewed Extraction to a committed
Gold Extraction regression fixture with the command-line step (not the browser):

```shell
uv run research-gap-dashboard promote-gold path/to/corpus
```
