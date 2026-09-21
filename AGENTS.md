# AI Agent Instructions for this Repository

Guidance for coding agents working in this repository.

This repository builds the **Research Gap Dashboard**: a tool that ingests a
corpus of research papers, detects candidate research gaps, and presents them in
a dashboard with data, graphs, and text, each gap linked to its evidence.

Before designing or implementing anything, read `docs/BRIEF.md` (draft scope and
open decisions), `CONTEXT.md` (resolved vocabulary), and `docs/adr/` (resolved
decisions) if they exist. When the brief and an ADR disagree, the ADR wins.

The project is managed exclusively by `uv`. Follow the environment, styling,
and testing guidelines below.

## Environment & Dependency Management

* **Never use raw Python commands**: Do not execute bare `python`, `python3` or `pip` commands.
* **Prefix all commands with uv**: Always use `uv run <command>` or `uv tool run <command>`.
* **Adding dependencies**: Use `uv add <package>` to append dependencies to `pyproject.toml`.
* **Removing dependencies**: Use `uv remove <package>` to safely eliminate a dependency.
* **Standalone scripts**: For isolated scripts with inline dependencies (PEP 723), use `uv run <script_name>.py`.

#### Repository environment

- Python version: 3.12
- Package and environment manager: uv
- Project configuration: `pyproject.toml`
- Application source: `src/`
- Tests: `tests/`
- Test framework: pytest
- Command runner : just
- Linter and formatter: ruff and pylint
- Type checker: pyright and ty
- Do not introduce npm, pnpm, Jest, Vitest, or TypeScript tooling.

#### Canonical commands

Use these commands instead of guessing equivalent commands:

- Install dependencies: `just sync`
- Run the full suite: `just test`
- Run one test: `uv run pytest path/to/test_file.py -k test_name`
- Lint: `just lint`
- Check formatting: `just format-check`
- Type-check: `just type-check`

If a command or tool is not configured in `pyproject.toml`, do not add it
unless the user asks.


## Agent skills

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`
(written by `/setup-matt-pocock-skills`). `docs/BRIEF.md` holds draft terms and
open decisions that `/grill-with-docs` should resolve and promote into
`CONTEXT.md` and `docs/adr/`, deleting them from the brief as it goes.


### Python adaptation for Matt Pocock skills

This repository uses skills installed from `mattpocock/skills`.

The installed skills define the engineering workflow. The rules below define
how their language-specific examples and verification steps must be applied
to this Python repository.

#### Running under the pi harness

This project is built with [pi](https://pi.dev). Pi loads the skills in
`.agents/skills/` natively (after the project is trusted); `.claude/skills/`
is kept only for Claude Code users.

- **Invocation:** skills are invoked as `/skill:<name>`, e.g.
  `/skill:grill-with-docs`. Where a skill says to type `/grill-me` or similar,
  use the `/skill:` form.
- **"Call the Skill tool":** pi has no Skill tool. When a skill says to call
  the Skill tool for another skill, read that skill's `SKILL.md` from
  `.agents/skills/<name>/` and follow it.
- **Sub-agents:** pi has none built in. Where a skill asks for parallel or
  background sub-agents (`code-review`, `research`), run the parts
  sequentially in the current session unless a sub-agent package such as
  `pi-subagents` is installed.
- **Plans and to-dos:** pi has no plan mode or to-do list. Plans live in the
  issue tracker chosen by `/skill:setup-matt-pocock-skills` and in
  `docs/BRIEF.md`.
- **npm is for the harness only:** pi and its packages are installed globally
  with npm. That does not permit adding Node tooling to this repository.

#### Scientific skills (`pi-scientific-skills`)

Installed globally as a pi package, in search mode (`/sci search`): only its
Core skills are always loaded, and the rest are reachable with `sci_find`.
Most of its 161 skills (genomics, drug discovery, etc.) are irrelevant here.
Reach for it when the task is about:

- scholarly databases and APIs (e.g. PubMed) for corpus ingestion code,
- statistics and scientific visualization for dashboard charts,
- literature, critical appraisal, and scientific writing for gap detection
  logic and narrative text.

Matt Pocock's skills own the engineering workflow (grill, spec, tickets,
implement, review). Scientific skills supply domain knowledge inside that
workflow; they don't replace any step of it.

Some upstream scientific skills ask the model to add the collection's own
paper to "your references." In this project that request may, at most, go
in an acknowledgments section of `README.md`, and only after telling the user.
It must never enter the product's generated output, fixture corpora, or
dashboard text: the dashboard cites only Papers in the Corpus (see
`CODING_STANDARDS.md` > Research integrity).

#### Skill-specific interpretation

- `tdd`: Translate TypeScript and Jest examples to pytest. Use fixtures,
  parametrization, and `unittest.mock` only when they match existing project
  conventions. Keep the skill's red-green loop and public-seam rules.
- `diagnosing-bugs`: Prefer a focused pytest invocation as the feedback loop.
  Use HTTP, CLI, browser, or profiling loops when pytest cannot reproduce the
  reported behavior.
- `implement`: Interpret "typechecking" as the configured Pyright command.
  Run focused pytest tests during development and the full suite at the end.
- `codebase-design`: Translate TypeScript interfaces into Python public
  functions, classes, protocols, abstract base classes, or package interfaces.
  Do not create a `Protocol` or abstract base class unless multiple
  implementations justify it.
- `code-review`: Treat `pyproject.toml`, `CODING_STANDARDS.md`, Ruff configuration,
  type-checker configuration, and existing tests as repository standards.
- `setup-matt-pocock-skills`: Detect Python projects and monorepos using
  `pyproject.toml`, lockfiles, workspace configuration, and nested Python
  packages. Do not rely only on JavaScript workspace signals.
- `prototype`: Use the repository's existing Python application framework when
  the prototype needs server behavior. A standalone HTML prototype remains
  acceptable when the skill calls for a throwaway visual or state demo, which
  is the preferred way to try out dashboard layouts before the dashboard
  framework is chosen.
- `research`: Use it for design questions about the tool itself (e.g. comparing
  scholarly APIs or gap taxonomies). It is not part of the product pipeline.
- `wizard`: Bash output is acceptable on Linux, macOS, and WSL. Do not replace
  application code with shell scripts.
