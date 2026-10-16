"""
Record, snapshot, or live-run the chat evaluation set (issue #99).

The test suite replays `tests/fixtures/chat-eval/` offline; this script is the
manual, explicit step that talks to the live model (needs `ANTHROPIC_API_KEY`).

- `uv run scripts/record_chat_eval.py` re-records every eval question's chat
  completion into `llm-responses.json`, keyed by request fingerprint, for
  `RecordedLlmClient`. Run it after the chat prompt, retrieval, or the cases
  change; it logs each case's verdict, so a failing re-record is visible before
  it is committed.
- `--snapshot CORPUS` rewrites `context.json` from an extracted Corpus (its
  Papers and fact pool; no Candidate Gaps, so the eval measures fact grounding
  and isn't churned by gap detection). Re-record afterwards.
- `--live CORPUS --cases FILE` runs a cases file against a real Corpus with the
  context the Analytics page builds (gaps included), logs the verdicts, and
  writes nothing: the end-to-end acceptance check.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from research_gap_dashboard.analytics import GroundingContext
from research_gap_dashboard.chat_eval import (
  ChatEvalCase,
  EvalReport,
  grounded_context_for,
  load_eval_set,
  run_eval,
)
from research_gap_dashboard.dashboard.artifacts import (
  has_candidate_gaps,
  load_manifest,
)
from research_gap_dashboard.dashboard.fact_pool import corpus_grounding_context
from research_gap_dashboard.env import load_env
from research_gap_dashboard.llm import (
  AnthropicClient,
  LlmConfig,
  ModelTier,
  request_fingerprint,
)

logger = logging.getLogger("record_chat_eval")

ROOT = Path("tests/fixtures/chat-eval")


class _RecordingClient:  # pylint: disable=too-few-public-methods
  """Passes each call to the live client and keeps its result by fingerprint."""

  name = "recording"

  def __init__(self, inner: AnthropicClient):
    self._inner = inner
    self.recorded: dict[str, dict[str, Any]] = {}

  def complete(
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: ModelTier = "default",
    refresh: bool = False,
  ) -> dict[str, Any]:
    """Complete live and record the answer (a retry overwrites a misfire)."""
    result = self._inner.complete(
      prompt, schema, prompt_version=prompt_version, tier=tier, refresh=refresh
    )
    self.recorded[request_fingerprint(prompt, schema, tier, prompt_version)] = result
    return result


def _log_report(
  report: EvalReport, cases: list[ChatEvalCase], context: GroundingContext
) -> None:
  """Log each case's retrieval size, answer, citations, and verdict."""
  by_id = {case.id: case for case in cases}
  for verdict in report.verdicts:
    grounded = grounded_context_for(by_id[verdict.case_id], context)
    status = "PASS" if verdict.passed else "FAIL"
    logger.info(
      "%s %s (%d facts retrieved): cited %s",
      status,
      verdict.case_id,
      len(grounded.facts),
      ", ".join(verdict.citations) or "nothing",
    )
    logger.info("  answer: %s", verdict.answer)
    for failure in verdict.failures:
      logger.info("  failure: %s", failure)
  passed = sum(verdict.passed for verdict in report.verdicts)
  logger.info("%d/%d cases passed", passed, len(report.verdicts))


def _snapshot(corpus: Path) -> None:
  """Rewrite the fixture's context from an extracted Corpus's Papers and facts."""
  context = corpus_grounding_context(corpus, load_manifest(corpus), has_gaps=False)
  (ROOT / "context.json").write_text(
    context.model_dump_json(indent=1, include={"papers", "fact_pool"}) + "\n",
    encoding="utf-8",
  )
  logger.info(
    "Snapshot %d Papers and %d facts from %s",
    len(context.papers),
    len(context.fact_pool),
    corpus,
  )


def _record() -> EvalReport:
  """Re-record the fixture's completions live and write them by fingerprint."""
  eval_set = load_eval_set(ROOT)
  client = _RecordingClient(AnthropicClient(LlmConfig.from_env()))
  report = run_eval(eval_set.cases, eval_set.context, client)
  (ROOT / "llm-responses.json").write_text(
    json.dumps(client.recorded, indent=1, ensure_ascii=False) + "\n",
    encoding="utf-8",
  )
  _log_report(report, eval_set.cases, eval_set.context)
  return report


def _live(corpus: Path, cases_path: Path) -> EvalReport:
  """Run a cases file live against a real Corpus, as the Analytics page would."""
  context = corpus_grounding_context(
    corpus, load_manifest(corpus), has_candidate_gaps(corpus)
  )
  cases = [
    ChatEvalCase.model_validate(case)
    for case in json.loads(cases_path.read_text(encoding="utf-8"))
  ]
  logger.info(
    "%s: %d Papers, %d facts in the pool, %d Candidate Gaps",
    corpus,
    len(context.papers),
    len(context.fact_pool),
    len(context.gaps),
  )
  report = run_eval(cases, context, AnthropicClient(LlmConfig.from_env()))
  _log_report(report, cases, context)
  return report


def main() -> None:
  """Parse the mode and run it; exit non-zero when a case fails."""
  parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
  parser.add_argument("--snapshot", type=Path, help="Corpus to snapshot context from.")
  parser.add_argument("--live", type=Path, help="Corpus to run --cases against live.")
  parser.add_argument("--cases", type=Path, help="Cases JSON for --live.")
  args = parser.parse_args()
  logging.basicConfig(level=logging.INFO, format="%(message)s")
  load_env()
  if args.snapshot:
    _snapshot(args.snapshot)
    return
  if args.live:
    if args.cases is None:
      parser.error("--live needs --cases")
    report = _live(args.live, args.cases)
  else:
    report = _record()
  sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
  main()
