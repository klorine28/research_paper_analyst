"""
Record live LLM screening completions for the saturation fixture Corpus.

Run manually (`uv run scripts/record_saturation_screening.py`, needs
`ANTHROPIC_API_KEY`) when the screening prompt or the fixture changes; the test
suite never calls the LLM. It replays the fixture's recorded OpenAlex and PubMed
answers (`source-responses.json`), runs the known-filled gap's check with LLM
screening on the cheap tier, and writes every completion to
`tests/fixtures/saturation-corpus/llm-responses.json`, keyed by request
fingerprint, for `RecordedLlmClient`. The check itself runs on a scratch copy,
so the fixture never gains a `saturation_checks.json`.
"""

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from research_gap_dashboard.aggregate import load_pipeline_taxonomies
from research_gap_dashboard.env import load_env
from research_gap_dashboard.llm import (
  AnthropicClient,
  LlmConfig,
  ModelTier,
  request_fingerprint,
)
from research_gap_dashboard.saturate import HitScreener, saturate_gap
from research_gap_dashboard.sources import OpenAlexAdapter, PubMedAdapter
from research_gap_dashboard.taxonomy import SEED_PATHS

logger = logging.getLogger("record_saturation_screening")

ROOT = Path("tests/fixtures/saturation-corpus")
SEARCH_LIMIT = 10
FILLED_GAP = "topicxtopic:heart-failure:myocardial-ischemia"
TIER: ModelTier = "cheap"


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
    """Complete live and record the answer under the request fingerprint."""
    result = self._inner.complete(
      prompt, schema, prompt_version=prompt_version, tier=tier, refresh=refresh
    )
    self.recorded[request_fingerprint(prompt, schema, tier, prompt_version)] = result
    return result


def main() -> None:
  """Run the filled gap's check with live screening and record the completions."""
  logging.basicConfig(level=logging.INFO, format="%(message)s")
  load_env()
  recorded = json.loads((ROOT / "source-responses.json").read_text(encoding="utf-8"))
  sources = [
    OpenAlexAdapter(fetch=lambda url: recorded["json"][url]),
    PubMedAdapter(
      fetch=lambda url: recorded["json"][url],
      fetch_text=lambda url: recorded["text"][url],
    ),
  ]
  config = LlmConfig.from_env()
  client = _RecordingClient(AnthropicClient(config))
  screener = HitScreener(client, model=config.model_for(TIER), tier=TIER)
  taxonomies = load_pipeline_taxonomies(list(SEED_PATHS.values()))
  with tempfile.TemporaryDirectory() as scratch:
    corpus = Path(shutil.copytree(ROOT, Path(scratch) / ROOT.name))
    check = saturate_gap(
      corpus,
      FILLED_GAP,
      sources,
      taxonomies,
      limit=SEARCH_LIMIT,
      screener=screener,
    )
  logger.info("%s: %s", FILLED_GAP, check.status_reason)
  for hit in check.hits:
    if hit.llm_verdict:
      logger.info("  %s %s: %s", hit.llm_verdict, hit.title, hit.llm_reason)
  (ROOT / "llm-responses.json").write_text(
    json.dumps(client.recorded, indent=1, ensure_ascii=False), encoding="utf-8"
  )


if __name__ == "__main__":
  main()
