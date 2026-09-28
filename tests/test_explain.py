"""Behavior of the explain stage: parsed sections in, two-register prose out."""

import shutil
from pathlib import Path
from typing import Any

import pytest

from conftest import StubLlmClient

from research_gap_dashboard import cli
from research_gap_dashboard.cli import main
from research_gap_dashboard.explain import (
  EXPLANATION_SUFFIX,
  ExplanationRegisters,
  explain_corpus,
  explain_paper,
  read_paper_explanation,
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
  ]


def _parsed_sample() -> ParsedPaper:
  return ParsedPaper(source_pdf=Path("sample.pdf"), sections=_sample_sections())


# A schema-valid explanation with both registers filled.
_BOTH_REGISTERS: dict[str, Any] = {
  "domain_explanation": (
    "A retrospective cohort of 200 patients assessed all-cause mortality, "
    "reported at 12%."
  ),
  "lay_explanation": (
    "The researchers looked back at records for 200 patients and found that "
    "12% of them died."
  ),
}


class _MisfireClient:  # pylint: disable=too-few-public-methods
  """Returns an empty object first, then the grounded explanation (misfire+retry)."""

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


def test_explanation_carries_both_registers() -> None:
  """A Paper's explanation holds a domain and a lay register, both filled."""
  explanation = explain_paper(
    "hanna2019", _parsed_sample(), StubLlmClient(_BOTH_REGISTERS)
  )

  assert set(ExplanationRegisters.model_fields) == {
    "domain_explanation",
    "lay_explanation",
  }
  assert explanation.registers.domain_explanation
  assert explanation.registers.lay_explanation
  assert explanation.citation_key == "hanna2019"
  assert explanation.prompt_version == "explain-v1"


def test_prompt_is_grounded_in_the_paper_text() -> None:
  """The prompt carries the Paper's own text so explanations are grounded in it."""
  client = StubLlmClient(_BOTH_REGISTERS)

  explain_paper("hanna2019", _parsed_sample(), client)

  # StubLlmClient does not capture prompts, so assert via a capturing client.
  captured: dict[str, str] = {}

  class _CapturingClient(StubLlmClient):  # pylint: disable=too-few-public-methods
    def complete(
      self, prompt, schema, *, prompt_version, tier="default", refresh=False
    ):
      captured["prompt"] = prompt
      return super().complete(
        prompt, schema, prompt_version=prompt_version, tier=tier, refresh=refresh
      )

  explain_paper("hanna2019", _parsed_sample(), _CapturingClient(_BOTH_REGISTERS))
  assert "A retrospective cohort of 200 patients." in captured["prompt"]
  assert "Mortality was 12%." in captured["prompt"]


def test_a_blank_misfire_is_retried_not_accepted() -> None:
  """An all-empty structured response is retried instead of yielding blank prose."""
  client = _MisfireClient(_BOTH_REGISTERS)

  explanation = explain_paper("hanna2019", _parsed_sample(), client)

  assert client.calls == 2
  assert explanation.registers.domain_explanation
  assert explanation.registers.lay_explanation


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


def test_explain_corpus_writes_one_file_per_paper(parsed_corpus: Path) -> None:
  """Each parsed Paper becomes an explanation file under paper-data/."""
  report = explain_corpus(parsed_corpus, StubLlmClient(_BOTH_REGISTERS))

  assert len(report.explanation_paths) == 10
  assert report.failures == []
  written = list((parsed_corpus / "paper-data").glob(f"*{EXPLANATION_SUFFIX}"))
  assert len(written) == 10

  manifest = read_manifest(parsed_corpus)
  reread = read_paper_explanation(parsed_corpus, manifest.papers[0].citation_key)
  assert reread.registers.domain_explanation
  assert reread.registers.lay_explanation


def test_a_paper_without_parsed_text_is_a_failure(parsed_corpus: Path) -> None:
  """A Paper that was never parsed is recorded as a failure, not skipped silently."""
  next((parsed_corpus / "paper-data").glob("*.parsed.json")).unlink()

  report = explain_corpus(parsed_corpus, StubLlmClient(_BOTH_REGISTERS))

  assert len(report.explanation_paths) == 9
  assert len(report.failures) == 1
  assert "run `parse`" in report.failures[0].reason


def test_reruns_hit_the_cache_and_make_no_new_calls(parsed_corpus: Path) -> None:
  """A second explain run serves from the disk cache without calling the LLM."""
  inner = StubLlmClient(_BOTH_REGISTERS)
  client = CachingLlmClient(inner, DiskCache(parsed_corpus / ".llm-cache"))

  explain_corpus(parsed_corpus, client)
  assert inner.calls == 10

  explain_corpus(parsed_corpus, client)
  assert inner.calls == 10


def test_cli_explain_requires_an_api_key(parsed_corpus: Path, monkeypatch) -> None:
  """Without an API key the explain command fails loudly rather than degrading."""
  monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

  assert main(["explain", str(parsed_corpus)]) == cli.EXIT_ERROR
