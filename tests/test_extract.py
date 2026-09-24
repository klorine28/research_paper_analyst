"""Behavior of the extract stage: parsed sections in, verified structured facts out."""

import shutil
from pathlib import Path
from typing import Any

import pytest

from research_gap_dashboard import cli
from research_gap_dashboard.cli import main
from research_gap_dashboard.extract import (
  EvidenceVerificationError,
  ExtractionFields,
  extract_corpus,
  extract_paper,
  read_extractions,
)
from research_gap_dashboard.ingest import ingest_corpus, read_manifest
from research_gap_dashboard.llm import CachingLlmClient, DiskCache
from research_gap_dashboard.parsing import PARSED_SUFFIX, ParsedPaper, ParsedSection


def _sample_sections(
  front_matter: str = "A study of heart failure.",
) -> list[ParsedSection]:
  """Build a Paper's sections, the front matter kept distinct per Paper."""
  return [
    ParsedSection(label="front_matter", heading="", text=front_matter),
    ParsedSection(
      label="abstract",
      heading="Abstract",
      text="We studied outcomes in older patients.",
    ),
    ParsedSection(
      label="methods",
      heading="Methods",
      text="A retrospective cohort of 200 patients.",
    ),
    ParsedSection(label="results", heading="Results", text="Mortality was 12%."),
    ParsedSection(
      label="limitations",
      heading="Limitations",
      text="The cohort was single-center.",
    ),
    ParsedSection(
      label="future_work",
      heading="Future Directions",
      text="A multi-center trial is warranted.",
    ),
  ]


def _parsed_sample() -> ParsedPaper:
  return ParsedPaper(source_pdf=Path("sample.pdf"), sections=_sample_sections())


# A schema-valid extraction whose every passage is verbatim in the sample text.
_GROUNDED_FIELDS: dict[str, Any] = {
  "research_question": [
    {
      "statement": "Outcomes in older heart-failure patients.",
      "evidence": {
        "passage": "We studied outcomes in older patients.",
        "section": "abstract",
      },
    }
  ],
  "methods": [
    {
      "statement": "Retrospective cohort study.",
      "evidence": {
        "passage": "A retrospective cohort of 200 patients.",
        "section": "methods",
      },
    }
  ],
  "populations": [],
  "datasets": [],
  "key_findings": [
    {
      "statement": "Mortality was 12%.",
      "evidence": {"passage": "Mortality was 12%.", "section": "results"},
    }
  ],
  "limitations": [
    {
      "statement": "Single-center cohort.",
      "evidence": {
        "passage": "The cohort was single-center.",
        "section": "limitations",
      },
    }
  ],
  "future_work": [
    {
      "statement": "A multi-center trial is needed.",
      "evidence": {
        "passage": "A multi-center trial is warranted.",
        "section": "future_work",
      },
    }
  ],
}


class _StubClient:  # pylint: disable=too-few-public-methods
  """A keyless LlmClient returning a fixed result and counting its calls."""

  name = "stub"

  def __init__(self, result: dict[str, Any]):
    self.result = result
    self.calls = 0

  def complete(  # pylint: disable=unused-argument
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: str = "default",
  ) -> dict[str, Any]:
    """Count the call and return the canned extraction."""
    self.calls += 1
    return self.result


@pytest.fixture(name="parsed_corpus")
def parsed_corpus_fixture(cardiology_corpus: Path, tmp_path: Path) -> Path:
  """Copy the fixture Corpus, ingest it, and write distinct parsed text per Paper."""
  target = tmp_path / "corpus"
  shutil.copytree(cardiology_corpus, target)
  ingest_corpus(target)
  paper_data = target / "paper-data"
  # Distinct front matter per Paper so prompts (and LLM cache keys) differ.
  for paper in read_manifest(target).papers:
    parsed = ParsedPaper(
      source_pdf=paper.pdf_path,
      sections=_sample_sections(front_matter=f"Study {paper.citation_key}."),
    )
    (paper_data / f"{paper.citation_key}{PARSED_SUFFIX}").write_text(
      parsed.model_dump_json(indent=2), encoding="utf-8"
    )
  return target


def test_extraction_holds_all_seven_fields_with_evidence() -> None:
  """A grounded extraction carries all seven fields, each fact with a passage."""
  extraction = extract_paper(
    "hanna2019", _parsed_sample(), _StubClient(_GROUNDED_FIELDS)
  )

  assert len(ExtractionFields.model_fields) == 7
  assert extraction.fields.research_question[0].evidence.passage
  assert extraction.fields.methods[0].evidence.passage
  assert extraction.fields.methods[0].evidence.section == "methods"
  assert extraction.fields.key_findings[0].evidence.passage


def test_evidence_passage_must_string_match_the_text() -> None:
  """A quoted passage absent from the paper text fails loudly, not silently."""
  invented = {**_GROUNDED_FIELDS}
  invented["key_findings"] = [
    {
      "statement": "Mortality was 50%.",
      "evidence": {"passage": "Mortality was 50%.", "section": "results"},
    }
  ]

  with pytest.raises(EvidenceVerificationError):
    extract_paper("hanna2019", _parsed_sample(), _StubClient(invented))


def test_empty_passage_fails_verification() -> None:
  """An empty Evidence passage is a verification failure, not an accepted fact."""
  empty = {**_GROUNDED_FIELDS}
  empty["limitations"] = [
    {"statement": "x", "evidence": {"passage": "  ", "section": "limitations"}}
  ]

  with pytest.raises(EvidenceVerificationError):
    extract_paper("hanna2019", _parsed_sample(), _StubClient(empty))


def test_passage_matches_across_line_wrapping() -> None:
  """Whitespace differences between quote and text do not break verification."""
  wrapped = {**_GROUNDED_FIELDS}
  wrapped["methods"] = [
    {
      "statement": "Retrospective cohort.",
      "evidence": {
        "passage": "A retrospective\n  cohort of 200 patients.",
        "section": "methods",
      },
    }
  ]

  extraction = extract_paper("hanna2019", _parsed_sample(), _StubClient(wrapped))

  assert extraction.fields.methods[0].statement == "Retrospective cohort."


def test_extract_corpus_writes_artifact_for_every_paper(parsed_corpus: Path) -> None:
  """Each parsed Paper becomes an Extraction in the single Extractions artifact."""
  report = extract_corpus(parsed_corpus, _StubClient(_GROUNDED_FIELDS))

  assert len(report.extractions) == 10
  assert report.failures == []

  reread = read_extractions(parsed_corpus)
  assert {e.citation_key for e in reread.extractions} == {
    e.citation_key for e in report.extractions
  }
  assert reread.prompt_version == "extract-v1"
  assert (parsed_corpus / "artifacts" / "extractions.json").is_file()


def test_a_bad_extraction_is_reported_without_aborting(parsed_corpus: Path) -> None:
  """A mismatch rejects that Paper only; the rest of the Corpus still extracts."""
  invented = {**_GROUNDED_FIELDS}
  invented["key_findings"] = [
    {
      "statement": "Invented.",
      "evidence": {"passage": "Not in the paper at all.", "section": "results"},
    }
  ]

  report = extract_corpus(parsed_corpus, _StubClient(invented))

  assert report.extractions == []
  assert len(report.failures) == 10
  assert all("not found" in failure.reason for failure in report.failures)


def test_a_paper_without_parsed_text_is_a_failure(parsed_corpus: Path) -> None:
  """A Paper that was never parsed is recorded as a failure, not skipped silently."""
  next((parsed_corpus / "paper-data").glob("*.parsed.json")).unlink()

  report = extract_corpus(parsed_corpus, _StubClient(_GROUNDED_FIELDS))

  assert len(report.extractions) == 9
  assert len(report.failures) == 1
  assert "run `parse`" in report.failures[0].reason


def test_reruns_hit_the_cache_and_make_no_new_calls(parsed_corpus: Path) -> None:
  """A second extract run serves from the disk cache without calling the LLM."""
  inner = _StubClient(_GROUNDED_FIELDS)
  client = CachingLlmClient(inner, DiskCache(parsed_corpus / ".llm-cache"))

  extract_corpus(parsed_corpus, client)
  assert inner.calls == 10

  extract_corpus(parsed_corpus, client)
  assert inner.calls == 10


def test_cli_extract_requires_an_api_key(parsed_corpus: Path, monkeypatch) -> None:
  """Without an API key the extract command fails loudly rather than degrading."""
  monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

  assert main(["extract", str(parsed_corpus)]) == cli.EXIT_ERROR
