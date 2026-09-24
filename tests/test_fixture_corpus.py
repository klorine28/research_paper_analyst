"""The committed cardiology fixture Corpus is usable and redistributable."""

from pathlib import Path
from typing import Any

from research_gap_dashboard.corpus_layout import inspect_corpus_layout

REDISTRIBUTABLE_LICENSES = {"cc-by", "cc0"}


def test_fixture_corpus_follows_the_layout_convention(cardiology_corpus: Path):
  """Downstream stages can point at the fixture Corpus as-is."""
  report = inspect_corpus_layout(cardiology_corpus)

  assert report.problems == []
  assert report.paper_list_path == cardiology_corpus / "corpus.bib"
  assert 5 <= len(report.pdf_paths) <= 10


def test_every_fixture_pdf_has_recorded_provenance(
  cardiology_corpus: Path, cardiology_provenance: list[dict[str, Any]]
):
  """Every committed PDF names its DOI, licence, and where it came from."""
  report = inspect_corpus_layout(cardiology_corpus)

  assert {record["file"] for record in cardiology_provenance} == {
    path.name for path in report.pdf_paths
  }
  for record in cardiology_provenance:
    assert record["license"] in REDISTRIBUTABLE_LICENSES
    assert record["doi"].startswith("10.")
    assert record["source_url"].startswith("https://")


def test_every_fixture_paper_has_a_bibtex_entry_with_its_doi(
  cardiology_corpus: Path, cardiology_provenance: list[dict[str, Any]]
):
  """The paper list covers the PDFs, so ingest can match one to the other."""
  bibtex = (cardiology_corpus / "corpus.bib").read_text(encoding="utf-8")

  assert bibtex.count("@article{") == len(cardiology_provenance)
  for record in cardiology_provenance:
    assert f"doi = {{{record['doi']}}}" in bibtex
