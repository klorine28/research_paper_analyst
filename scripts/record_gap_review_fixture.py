"""
Write the `gap-review-v1` fixture: a tiny synthetic Corpus and its review answers.

Run manually (`uv run scripts/record_gap_review_fixture.py`) when the review
prompt, its output shape, or the fixture Corpus changes; the test suite never
calls the LLM. No API key is needed: the answers are authored here, not
recorded live, because the fixture must hold cases a live model gives only by
chance (a ground id outside the supplied Evidence). The script

1. writes the Corpus's manifest and NormalizedFacts (ten fictional Papers, no
   limitation statements) under `tests/fixtures/gap-review/corpus/artifacts/`;
2. runs detect on a scratch copy with a client that answers each review from
   `ANSWERS` by the candidate id the prompt names; and
3. writes every answer to `tests/fixtures/gap-review/llm-responses.json`, keyed
   by request fingerprint, for `RecordedLlmClient`.

Five Papers study Takotsubo with retrospective designs and five study heart
failure in randomized trials, so exactly three cells reach Medium or above and
are reviewed: one is kept, one rejected as pseudo-firstness, and one kept as
weakly supported, citing a ground id that is not in its Evidence.
"""

import json
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from research_gap_dashboard.aggregate import (
  AggregateReport,
  CategoryAssignment,
  NormalizedFacts,
)
from research_gap_dashboard.detect import detect_corpus
from research_gap_dashboard.extract import Evidence, ExtractReport
from research_gap_dashboard.ingest import CorpusManifest, Paper
from research_gap_dashboard.llm import ModelTier, request_fingerprint
from research_gap_dashboard.review import REVIEW_PROMPT_VERSION

logger = logging.getLogger("record_gap_review_fixture")

FIXTURE = Path("tests/fixtures/gap-review")
CORPUS = FIXTURE / "corpus"

_TOPICS = {
  "takotsubo": ("Takotsubo Cardiomyopathy", "stress cardiomyopathy"),
  "heart-failure": ("Heart Failure", "chronic heart failure"),
}
_METHODS = {
  "retrospective": ("Retrospective Studies", "retrospective chart review"),
  "rct": ("Randomized Controlled Trial", "randomized trial"),
}
# (citation key, topic, method, year)
_PAPERS = [
  *((f"tako{i}", "takotsubo", "retrospective", 2015 + i) for i in range(1, 6)),
  *((f"hf{i}", "heart-failure", "rct", 2015 + i) for i in range(1, 6)),
]

ANSWERS: dict[str, dict[str, Any]] = {
  "topicxmethod:takotsubo:rct": {
    "verdict": "keep",
    "rejection_class": None,
    "claim": "No Paper in the Corpus tests a Takotsubo treatment in a "
    "randomized trial.",
    "grounds": ["tako1:research_question[0]", "tako2:research_question[0]"],
    "warrant": "Five Papers study Takotsubo, all retrospectively, while "
    "randomized trials appear only for heart failure.",
    "audit_basis": {
      "text": "Takotsubo is studied only by retrospective chart reviews; "
      "randomized trials in the Corpus study heart failure.",
      "evidence_ids": ["tako1:research_question[0]", "hf1:study_design[0]"],
    },
    "minimal_study": "A pilot randomized trial of a beta-blocker against usual "
    "care after a Takotsubo episode.",
    "bucket": "more_probable",
  },
  "topicxtopic:heart-failure:takotsubo": {
    "verdict": "reject",
    "rejection_class": "pseudo_firstness",
    "claim": None,
    "grounds": [],
    "warrant": "Being first to describe heart failure alongside Takotsubo in "
    "this Corpus names no question a study would answer.",
    "audit_basis": None,
    "minimal_study": None,
    "bucket": None,
  },
  "topicxmethod:heart-failure:retrospective": {
    "verdict": "keep",
    "rejection_class": None,
    "claim": "No Paper in the Corpus reviews heart failure retrospectively.",
    "grounds": ["hf1:research_question[0]", "zz9999:methods[0]"],
    "warrant": "Heart failure is studied only in trials, but registry reviews "
    "of it are common, so the hole may reflect the Corpus's selection.",
    "audit_basis": {
      "text": "All five heart failure Papers are randomized trials.",
      "evidence_ids": ["hf2:research_question[0]"],
    },
    "minimal_study": "A retrospective registry review of heart failure admissions.",
    "bucket": "least_probable",
  },
}

_CANDIDATE_ID = re.compile(r"^CANDIDATE ID: (.+)$", re.MULTILINE)


class _AuthoringClient:  # pylint: disable=too-few-public-methods
  """Answers each review from `ANSWERS` and keeps it by fingerprint."""

  name = "authoring"

  def __init__(self) -> None:
    self.recorded: dict[str, dict[str, Any]] = {}

  def complete(  # pylint: disable=unused-argument
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: ModelTier = "default",
    refresh: bool = False,
  ) -> dict[str, Any]:
    """Return the authored answer for the candidate this prompt reviews."""
    if prompt_version != REVIEW_PROMPT_VERSION:
      raise RuntimeError(f"unexpected prompt {prompt_version}")
    found = _CANDIDATE_ID.search(prompt)
    if found is None or found.group(1) not in ANSWERS:
      raise RuntimeError("the fixture reviews a candidate with no authored answer")
    answer = ANSWERS[found.group(1)]
    self.recorded[request_fingerprint(prompt, schema, tier, prompt_version)] = answer
    return answer


def _assignment(
  axis: Any, category_id: str, names: tuple[str, str], key: str, field: str
) -> CategoryAssignment:
  """Place one Paper on one category, backed by a synthetic passage."""
  label, term = names
  return CategoryAssignment(
    axis=axis,
    original_term=term,
    category_id=category_id,
    category_label=label,
    fact_ref=f"[{field}[0]]",
    evidence=Evidence(
      passage=f"This {term} ({key}) is described in the methods.", section="methods"
    ),
  )


def write_corpus(root: Path) -> None:
  """Write the fixture Corpus's manifest, Extractions and NormalizedFacts."""
  artifacts = root / "artifacts"
  artifacts.mkdir(parents=True, exist_ok=True)
  manifest = CorpusManifest(
    corpus_root=Path("."),
    papers=[
      Paper(
        citation_key=key,
        doi=f"10.0000/{key}",
        pdf_path=Path(f"papers/{key}.pdf"),
        year=year,
      )
      for key, _, _, year in _PAPERS
    ],
    unmatched_entries=[],
    orphan_pdfs=[],
  )
  normalized = AggregateReport(
    corpus_root=Path("."),
    prompt_version="aggregate-fixture",
    tier="default",
    taxonomies=[],
    normalized=[
      NormalizedFacts(
        citation_key=key,
        assignments=[
          _assignment("topic", topic, _TOPICS[topic], key, "research_question"),
          _assignment("method", method, _METHODS[method], key, "study_design"),
        ],
        unmapped=[],
      )
      for key, topic, method, _ in _PAPERS
    ],
    failures=[],
  )
  extracted = ExtractReport(
    corpus_root=Path("."),
    prompt_version="extract-fixture",
    tier="default",
    extractions=[],
    failures=[],
  )
  for name, report in (
    ("corpus-manifest.json", manifest),
    ("normalized_facts.json", normalized),
    ("extractions.json", extracted),
  ):
    (artifacts / name).write_text(report.model_dump_json(indent=2), encoding="utf-8")


def main() -> None:
  """Write the fixture Corpus, then author and key every review answer."""
  logging.basicConfig(level=logging.INFO, format="%(message)s")
  write_corpus(CORPUS)
  client = _AuthoringClient()
  with tempfile.TemporaryDirectory() as scratch:
    copy = Path(scratch) / "corpus"
    shutil.copytree(CORPUS, copy)
    detect_corpus(copy, client)
  if len(client.recorded) != len(ANSWERS):
    raise RuntimeError(
      f"expected {len(ANSWERS)} reviews, recorded {len(client.recorded)}"
    )
  path = FIXTURE / "llm-responses.json"
  path.write_text(
    json.dumps(client.recorded, indent=2, sort_keys=True) + "\n", encoding="utf-8"
  )
  logger.info("Wrote %d review answers to %s", len(client.recorded), path)


if __name__ == "__main__":
  main()
