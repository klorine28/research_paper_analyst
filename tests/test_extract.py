"""Behavior of the extract stage: parsed sections in, verified structured facts out."""

import shutil
from pathlib import Path
from typing import Any

import pytest

from conftest import StubLlmClient

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


class _MisfireClient:  # pylint: disable=too-few-public-methods
  """Returns an empty object first, then the grounded extraction (a misfire+retry)."""

  name = "misfire"

  def __init__(self, good: dict[str, Any]):
    self.good = good
    self.calls = 0

  def complete(  # pylint: disable=unused-argument
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: str = "default",
    refresh: bool = False,
  ) -> dict[str, Any]:
    """Return an empty object on the first call, the grounded one thereafter."""
    self.calls += 1
    return {} if self.calls == 1 else self.good


def test_an_empty_misfire_is_retried_not_accepted() -> None:
  """An all-empty structured response is retried instead of yielding no facts."""
  client = _MisfireClient(_GROUNDED_FIELDS)

  result = extract_paper("hanna2019", _parsed_sample(), client)

  assert client.calls == 2
  assert result.extraction.fields.methods[0].evidence.passage


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
  result = extract_paper("hanna2019", _parsed_sample(), StubLlmClient(_GROUNDED_FIELDS))
  extraction = result.extraction

  assert result.unverified == []
  assert len(ExtractionFields.model_fields) == 7
  assert extraction.fields.research_question[0].evidence.passage
  assert extraction.fields.methods[0].evidence.passage
  assert extraction.fields.methods[0].evidence.section == "methods"
  assert extraction.fields.key_findings[0].evidence.passage


def test_an_unverifiable_fact_is_dropped_not_the_whole_paper() -> None:
  """A quoted passage absent from the text drops that fact; verified facts remain."""
  invented = {**_GROUNDED_FIELDS}
  invented["key_findings"] = [
    {
      "statement": "Mortality was 50%.",
      "evidence": {"passage": "Mortality was 50%.", "section": "results"},
    }
  ]

  result = extract_paper("hanna2019", _parsed_sample(), StubLlmClient(invented))

  # The invented finding is dropped and recorded; the verified facts survive.
  assert result.extraction.fields.key_findings == []
  assert result.extraction.fields.methods[0].evidence.passage
  assert len(result.unverified) == 1
  dropped = result.unverified[0]
  assert dropped.field == "key_findings"
  assert "not found" in dropped.reason


def test_a_paper_with_no_verifiable_facts_is_rejected() -> None:
  """When every fact's Evidence fails, the Paper has nothing to show and is rejected."""
  invented = {name: [] for name in ExtractionFields.model_fields}
  invented["key_findings"] = [
    {
      "statement": "Mortality was 50%.",
      "evidence": {"passage": "Not in the paper at all.", "section": "results"},
    }
  ]

  with pytest.raises(EvidenceVerificationError):
    extract_paper("hanna2019", _parsed_sample(), StubLlmClient(invented))


def test_empty_passage_is_dropped_as_unverifiable() -> None:
  """An empty Evidence passage drops that fact rather than being accepted."""
  empty = {**_GROUNDED_FIELDS}
  empty["limitations"] = [
    {"statement": "x", "evidence": {"passage": "  ", "section": "limitations"}}
  ]

  result = extract_paper("hanna2019", _parsed_sample(), StubLlmClient(empty))

  assert result.extraction.fields.limitations == []
  assert [u.field for u in result.unverified] == ["limitations"]


def _one_finding(passage: str) -> dict[str, Any]:
  """Build a minimal grounded-shape extraction with a single key_findings fact."""
  fields = {name: [] for name in ExtractionFields.model_fields}
  fields["key_findings"] = [
    {"statement": "A finding.", "evidence": {"passage": passage, "section": "results"}}
  ]
  return fields


def _parsed_with(results_text: str) -> ParsedPaper:
  """Build a one-section Paper whose results carry the given (mangled) text."""
  return ParsedPaper(
    source_pdf=Path("sample.pdf"),
    sections=[ParsedSection(label="results", heading="Results", text=results_text)],
  )


def test_replacement_character_in_text_is_a_wildcard() -> None:
  """A glyph the parser lost (U+FFFD) matches the character a faithful quote keeps."""
  parsed = _parsed_with("Patients with CRP \ufffd3mg/L were compared.")

  result = extract_paper(
    "p", parsed, StubLlmClient(_one_finding("CRP \u22643mg/L were compared"))
  )

  assert result.unverified == []
  assert result.extraction.fields.key_findings[0].statement == "A finding."


def test_markdown_table_pipes_are_ignored_when_matching() -> None:
  """A quote of a table row matches though the parser kept the `|` cell delimiters."""
  parsed = _parsed_with("Group A | 10 mice | Male | Infarct size reduced")

  result = extract_paper(
    "p", parsed, StubLlmClient(_one_finding("Group A 10 mice Male"))
  )

  assert result.unverified == []


def test_a_typo_is_not_forgiven_by_matching() -> None:
  """Normalization undoes parser artifacts only; an LLM typo still fails to verify."""
  parsed = _parsed_with("prolongation by bradycardial pacing in rabbits")
  fields = _one_finding("pacing in rabbits")  # a verifiable fact keeps the paper
  fields["key_findings"].append(
    {
      "statement": "A typo.",
      "evidence": {
        "passage": "prolongation by brachycardial pacing",
        "section": "results",
      },
    }
  )

  result = extract_paper("p", parsed, StubLlmClient(fields))

  # "brachycardial" is a genuine misquote with no lost glyph to excuse it: it is
  # dropped, while the verbatim "pacing in rabbits" fact survives.
  assert len(result.extraction.fields.key_findings) == 1
  assert [u.statement for u in result.unverified] == ["A typo."]


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

  result = extract_paper("hanna2019", _parsed_sample(), StubLlmClient(wrapped))

  assert result.extraction.fields.methods[0].statement == "Retrospective cohort."
  assert result.unverified == []


def test_extract_corpus_writes_artifact_for_every_paper(parsed_corpus: Path) -> None:
  """Each parsed Paper becomes an Extraction in the single Extractions artifact."""
  report = extract_corpus(parsed_corpus, StubLlmClient(_GROUNDED_FIELDS))

  assert len(report.extractions) == 10
  assert report.failures == []

  reread = read_extractions(parsed_corpus)
  assert {e.citation_key for e in reread.extractions} == {
    e.citation_key for e in report.extractions
  }
  assert reread.prompt_version == "extract-v1"
  assert (parsed_corpus / "artifacts" / "extractions.json").is_file()


def test_an_unverifiable_fact_is_dropped_corpus_wide(parsed_corpus: Path) -> None:
  """One bad quote per Paper is dropped and recorded; every Paper still extracts."""
  invented = {**_GROUNDED_FIELDS}
  invented["key_findings"] = [
    {
      "statement": "Invented.",
      "evidence": {"passage": "Not in the paper at all.", "section": "results"},
    }
  ]

  report = extract_corpus(parsed_corpus, StubLlmClient(invented))

  assert len(report.extractions) == 10
  assert report.failures == []
  assert len(report.unverified) == 10
  assert all("not found" in u.reason for u in report.unverified)
  # The dropped facts round-trip through the artifact for later inspection.
  assert len(read_extractions(parsed_corpus).unverified) == 10


def test_a_paper_with_only_bad_quotes_is_a_failure(parsed_corpus: Path) -> None:
  """A Paper whose every fact fails verification is a failure, not an empty one."""
  all_bad = {name: [] for name in ExtractionFields.model_fields}
  all_bad["key_findings"] = [
    {
      "statement": "Invented.",
      "evidence": {"passage": "Not in the paper at all.", "section": "results"},
    }
  ]

  report = extract_corpus(parsed_corpus, StubLlmClient(all_bad))

  assert report.extractions == []
  assert len(report.failures) == 10
  assert all("no fact" in failure.reason for failure in report.failures)


def test_a_paper_without_parsed_text_is_a_failure(parsed_corpus: Path) -> None:
  """A Paper that was never parsed is recorded as a failure, not skipped silently."""
  next((parsed_corpus / "paper-data").glob("*.parsed.json")).unlink()

  report = extract_corpus(parsed_corpus, StubLlmClient(_GROUNDED_FIELDS))

  assert len(report.extractions) == 9
  assert len(report.failures) == 1
  assert "run `parse`" in report.failures[0].reason


def test_reruns_hit_the_cache_and_make_no_new_calls(parsed_corpus: Path) -> None:
  """A second extract run serves from the disk cache without calling the LLM."""
  inner = StubLlmClient(_GROUNDED_FIELDS)
  client = CachingLlmClient(inner, DiskCache(parsed_corpus / ".llm-cache"))

  extract_corpus(parsed_corpus, client)
  assert inner.calls == 10

  extract_corpus(parsed_corpus, client)
  assert inner.calls == 10


def test_cli_extract_signals_incompleteness_when_facts_drop(
  parsed_corpus: Path, monkeypatch
) -> None:
  """Dropped facts do not abort extract, but the CLI exits with the incomplete code."""
  invented = {**_GROUNDED_FIELDS}
  invented["key_findings"] = [
    {
      "statement": "Invented.",
      "evidence": {"passage": "Not in the paper at all.", "section": "results"},
    }
  ]
  monkeypatch.setattr(cli, "build_llm_client", lambda **_: StubLlmClient(invented))

  assert main(["extract", str(parsed_corpus)]) == cli.EXIT_INCOMPLETE


def test_cli_extract_requires_an_api_key(parsed_corpus: Path, monkeypatch) -> None:
  """Without an API key the extract command fails loudly rather than degrading."""
  monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

  assert main(["extract", str(parsed_corpus)]) == cli.EXIT_ERROR
