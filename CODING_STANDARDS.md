# Coding Standards

Standards for code in this repository. They describe what the codebase already
does; when in doubt, match the surrounding code. Developer-facing docs
(`CONTEXT.md`, `docs/`, `README.md`) are written in English.

## Language and vocabulary

- **Code identifiers are English.** Function names, variables, comments,
  commit messages, and tests are English. The language of dashboard text is
  an open decision (see `docs/BRIEF.md`); until an ADR settles it, keep all
  user-facing strings in one place so they can be translated later.
- **Use the glossary.** `CONTEXT.md` defines the canonical terms.
  If a concept you need isn't in the glossary, that's a signal to either
  reconsider the name or extend the glossary via `/domain-modeling`.

## Coding Conventions

* **Asynchronous Design**: Prefer `asyncio` for I/O operations and API network calls.
* **Data Validations**: Use `pydantic` v2 BaseModels for all schema parsing and object instantiations.
* **Data Manipulation**: Use `polars` instead of `pandas` if structural dataframe handling is required.
* **Logging Protocols**: Never use raw `print()` statements for system events. Use the standard library `logger.info()`, `logger.error()` or `logger.exception()`.

## Comments

- **Comments state what the code can't**: the constraint, the invariant, or
  the why — never a restatement of the next line. 
- **Modules open with a doc comment** saying what the module is for and, when
  relevant, what deliberately isn't there.
- Don't write comments that talk to a reviewer ("changed this because…");
  that context belongs in the commit message.

## Configuration

- Configuration is **read once from the environment and validated**. 
  Security-critical settings with no safe default are boot/run failures with an 
  explanation, not silent degradation.
- Every setting read must be consulted somewhere. A variable that is read but 
  never used gets removed, not documented.
- Use project root .env for variables/settings; This file should never be commited. 
- New variables get documented in `.env.example` and the READMEs, with
  their default and why they exist.
  
## Dependencies

- Python **= 3.12** — `pyproject.toml` enforces this .
- Adding a dependency is a decision, not a default: the dependency lists are
  deliberately short, and unused dependencies get removed. 
  Install using `uv add`; keep `uv.lock` committed.
- On a major-version upgrade, verify the surface the code actually uses and
  say so in the commit.

## Research integrity

These rules exist because researchers will act on what the dashboard says.

- **Every claim is traceable.** Any Candidate Gap, statistic, or sentence the
  dashboard shows must carry references to the Papers and Evidence it came
  from. Data models make those references required fields, not optional ones.
- **Never invent citations.** Generated text may only cite Papers in the
  Corpus. Validate every citation in generated text against the Corpus before
  rendering; drop or flag anything that doesn't resolve.
- **Candidates, not verdicts.** Detected gaps are presented as candidates with
  a confidence level and the reasoning behind it. Don't word output as if the
  tool has proven a gap exists.
- **Show the denominator.** Any count or chart states what it was computed
  over (how many papers, which sources, which years).
- **Stages are reproducible.** Each pipeline stage writes an inspectable
  artifact to disk. LLM or API calls are cached by their inputs, and the model
  name, prompt version, and parameters are recorded with every result, so a
  rerun on the same Corpus gives the same dashboard.
- **Test with fixtures, not live services.** Tests use small committed
  fixture corpora and recorded responses. Nothing in `just test` calls a
  paid API or the network.
