"""
Build the follow-up fixture Corpus and record its live LLM completions.

Run manually (`uv run scripts/record_follow_up_fixture.py`, needs
`ANTHROPIC_API_KEY`) when the limitation prompts or the fixture change; the test
suite never calls the LLM. It writes four synthetic Papers' artifacts to
`tests/fixtures/follow-up-corpus/artifacts/`, runs `detect` on a scratch copy
with the live model, and writes every completion to `llm-responses.json`, keyed
by request fingerprint, for `RecordedLlmClient`.

The Corpus holds one limitation a later Paper plainly addresses (a 2016
retrospective single-centre study asks for a prospective multicentre cohort; a
2021 Paper is one) and a control nobody addresses (emotional triggers were
never assessed). This is the regression fixture for issue #55.
"""

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from research_gap_dashboard.aggregate import AggregateReport
from research_gap_dashboard.detect import detect_corpus
from research_gap_dashboard.env import load_env
from research_gap_dashboard.extract import (
  Evidence,
  ExtractedFact,
  Extraction,
  ExtractionFields,
  ExtractReport,
)
from research_gap_dashboard.ingest import CorpusManifest, Paper
from research_gap_dashboard.parsing import SectionLabel
from research_gap_dashboard.llm import (
  AnthropicClient,
  LlmConfig,
  ModelTier,
  request_fingerprint,
)

logger = logging.getLogger("record_follow_up_fixture")

ROOT = Path("tests/fixtures/follow-up-corpus")
YEARS = {
  "early2016": 2016,
  "control2016": 2016,
  "unrelated2019": 2019,
  "later2021": 2021,
}


def _fact(statement: str, section: SectionLabel = "methods") -> ExtractedFact:
  """Build a fact whose Evidence quotes its own statement."""
  return ExtractedFact(
    statement=statement,
    evidence=Evidence(passage=statement, section=section),
  )


EXTRACTIONS = [
  Extraction(
    citation_key="early2016",
    fields=ExtractionFields(
      study_design=[
        _fact("Retrospective single-centre review of 48 Takotsubo patients.")
      ],
      key_findings=[
        _fact("In-hospital complications occurred in 21% of patients.", "results")
      ],
      limitations=[
        _fact(
          "The study was retrospective and limited to a single centre, so a "
          "prospective multicentre cohort is needed to confirm the findings.",
          "limitations",
        )
      ],
    ),
  ),
  Extraction(
    citation_key="control2016",
    fields=ExtractionFields(
      study_design=[_fact("Retrospective case series of 30 Takotsubo patients.")],
      limitations=[
        _fact(
          "Emotional and physical triggers preceding admission were not "
          "recorded, so their role in recurrence remains unknown.",
          "limitations",
        )
      ],
    ),
  ),
  Extraction(
    citation_key="unrelated2019",
    fields=ExtractionFields(
      study_design=[_fact("Case report of a single Takotsubo patient.")],
      key_findings=[_fact("The ECG showed transient ST-segment elevation.", "results")],
    ),
  ),
  Extraction(
    citation_key="later2021",
    fields=ExtractionFields(
      study_design=[
        _fact(
          "Prospective multicentre cohort study enrolling consecutive Takotsubo "
          "patients at 12 hospitals."
        )
      ],
      populations=[_fact("612 consecutive Takotsubo patients across 12 centres.")],
      methods=[_fact("Echocardiography was performed at admission and at 30 days.")],
      key_findings=[
        _fact(
          "In-hospital complications occurred in 18% of patients, confirming "
          "earlier single-centre estimates.",
          "results",
        )
      ],
    ),
  ),
]


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


def write_fixture() -> None:
  """Write the fixture Corpus's layout and upstream artifacts."""
  for name in ("papers", "paper-data", "judgments", "artifacts"):
    (ROOT / name).mkdir(parents=True, exist_ok=True)
  for name in ("papers", "paper-data", "judgments"):
    (ROOT / name / ".gitkeep").touch()
  (ROOT / "corpus.bib").write_text("", encoding="utf-8")
  manifest = CorpusManifest(
    corpus_root=ROOT,
    papers=[
      Paper(
        citation_key=key,
        doi=f"10.5555/{key}",
        title=f"Synthetic fixture Paper {key}",
        year=year,
        pdf_path=Path("papers") / f"{key}.pdf",
      )
      for key, year in YEARS.items()
    ],
    unmatched_entries=[],
    orphan_pdfs=[],
  )
  extractions = ExtractReport(
    corpus_root=ROOT,
    prompt_version="fixture",
    tier="default",
    extractions=EXTRACTIONS,
    failures=[],
  )
  normalized = AggregateReport(
    corpus_root=ROOT,
    prompt_version="fixture",
    tier="default",
    taxonomies=[],
    normalized=[],
    failures=[],
  )
  artifacts = ROOT / "artifacts"
  for name, report in (
    ("corpus-manifest.json", manifest),
    ("extractions.json", extractions),
    ("normalized_facts.json", normalized),
  ):
    (artifacts / name).write_text(
      report.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
  """Write the fixture, run detect live on a scratch copy, record completions."""
  logging.basicConfig(level=logging.INFO, format="%(message)s")
  load_env()
  write_fixture()
  client = _RecordingClient(AnthropicClient(LlmConfig.from_env()))
  with tempfile.TemporaryDirectory() as scratch:
    corpus = Path(shutil.copytree(ROOT, Path(scratch) / ROOT.name))
    report = detect_corpus(corpus, client)
  for group in report.limitation_groups:
    logger.info(
      "%s: %s", group.label, [follow_up.fact_id for follow_up in group.follow_ups]
    )
  (ROOT / "llm-responses.json").write_text(
    json.dumps(client.recorded, indent=1, ensure_ascii=False) + "\n",
    encoding="utf-8",
  )


if __name__ == "__main__":
  main()
