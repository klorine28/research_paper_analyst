"""
The dashboard's one seam onto the pipeline: run a CLI command as a subprocess.

ADR 0007 lets the dashboard *invoke* the pipeline but never *import* it, so the
front end stays swappable and `test_dashboard_imports_no_pipeline_logic` holds.
This module launches `uv run research-gap-dashboard <stage> <corpus>`, and owns
the three guards around that launch:

- a per-Corpus run-lock, because Streamlit reruns the script on every
  interaction and a double-click or a second tab must not start a concurrent,
  Corpus-corrupting run;
- a credential pre-check, so a stage that needs the LLM reports a missing key
  in the app instead of launching only to fail;
- an exit-code mapping, keeping "completed but incomplete" (code 2, ADR 0006)
  distinct from "failed" (code 1).

It is plain functions with no Streamlit dependency; the pages render the
`RunResult` it returns. Runs are blocking; live-streamed runs are out of scope.
"""

import fcntl
import logging
import os
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

CLI_COMMAND = ("uv", "run", "research-gap-dashboard")
API_KEY_ENV = "ANTHROPIC_API_KEY"

# Mirrors the CLI's exit codes (cli.EXIT_OK / EXIT_INCOMPLETE); restated rather
# than imported because the dashboard imports no pipeline module.
EXIT_OK = 0
EXIT_INCOMPLETE = 2

# The CLI commands that build an LLM client, which fails without a key even
# when every completion would be served from cache.
STAGES_NEEDING_KEY = frozenset({"extract", "explain", "aggregate", "detect"})

# The lock lives with the dashboard's other writes under `judgments/` (ADR
# 0003): it is neither a pipeline artifact nor a Corpus input.
LOCK_DIR = "judgments"
LOCK_NAME = ".pipeline-run.lock"


class RunOutcome(StrEnum):
  """How a requested run ended, as the researcher needs to tell them apart."""

  SUCCEEDED = "succeeded"
  INCOMPLETE = "incomplete"
  FAILED = "failed"
  LOCKED = "locked"
  MISSING_KEY = "missing_key"


class RunResult(BaseModel):
  """The outcome of one requested run, with the command and its output."""

  stage: str
  command: list[str]
  outcome: RunOutcome
  exit_code: int | None = None
  stdout: str = ""
  stderr: str = ""


class _CorpusLocked(Exception):
  """Another run already holds this Corpus."""


def build_command(corpus_root: Path, stage: str, args: Sequence[str] = ()) -> list[str]:
  """Return the argv that runs one CLI stage on a Corpus, with any stage options."""
  return [*CLI_COMMAND, stage, str(corpus_root.resolve()), *args]


def needs_api_key(stage: str) -> bool:
  """Whether running this stage requires the LLM credential."""
  return stage in STAGES_NEEDING_KEY


@contextmanager
def _corpus_lock(corpus_root: Path) -> Iterator[None]:
  """
  Hold an exclusive, non-blocking lock on a Corpus for the duration of a run.

  `flock` is held per open file, so it refuses a second Streamlit session in the
  same server process as well as another process, and the OS releases it if the
  dashboard dies mid-run, so a crash never leaves a Corpus locked for good.
  """
  lock_path = corpus_root / LOCK_DIR / LOCK_NAME
  lock_path.parent.mkdir(parents=True, exist_ok=True)
  with lock_path.open("a", encoding="utf-8") as handle:
    try:
      fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
      raise _CorpusLocked from error
    try:
      yield
    finally:
      fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _outcome_for(exit_code: int) -> RunOutcome:
  """Map a CLI exit code to its outcome; any unknown code is a failure."""
  if exit_code == EXIT_OK:
    return RunOutcome.SUCCEEDED
  if exit_code == EXIT_INCOMPLETE:
    return RunOutcome.INCOMPLETE
  return RunOutcome.FAILED


def run_stage(
  corpus_root: Path,
  stage: str,
  env: Mapping[str, str] | None = None,
  *,
  args: Sequence[str] = (),
) -> RunResult:
  """
  Run one CLI stage on a Corpus as a blocking subprocess and report how it ended.

  `env` defaults to the process environment (already populated from `.env` by
  the app shell); the subprocess inherits it unchanged. `args` are passed to the
  stage after the Corpus path (e.g. `--resolve` for ingest).
  """
  env = os.environ if env is None else env
  command = build_command(corpus_root, stage, args)
  if needs_api_key(stage) and not env.get(API_KEY_ENV):
    return RunResult(stage=stage, command=command, outcome=RunOutcome.MISSING_KEY)

  try:
    with _corpus_lock(corpus_root):
      logger.info("Running %s", " ".join(command))
      try:
        completed = subprocess.run(
          command, capture_output=True, text=True, check=False, env=dict(env)
        )
      except OSError as error:
        logger.error("Could not launch %s: %s", command[0], error)
        return RunResult(
          stage=stage,
          command=command,
          outcome=RunOutcome.FAILED,
          stderr=f"Could not launch {command[0]}: {error}",
        )
  except _CorpusLocked:
    return RunResult(stage=stage, command=command, outcome=RunOutcome.LOCKED)

  return RunResult(
    stage=stage,
    command=command,
    outcome=_outcome_for(completed.returncode),
    exit_code=completed.returncode,
    stdout=completed.stdout or "",
    stderr=completed.stderr or "",
  )
