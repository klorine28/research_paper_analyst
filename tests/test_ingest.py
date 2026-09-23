"""Behavior of the ingest stage: paper list plus PDFs in, CorpusManifest out."""

import json
import shutil
from pathlib import Path

import pytest

from research_gap_dashboard.cli import main
from research_gap_dashboard.ingest import CorpusSizeError, ingest_corpus


@pytest.fixture(name="corpus")
def corpus_fixture(cardiology_corpus: Path, tmp_path: Path) -> Path:
  """Copy the fixture Corpus so a test can add or remove files freely."""
  target = tmp_path / "corpus"
  shutil.copytree(cardiology_corpus, target)
  return target


def test_manifest_lists_every_matched_paper(corpus: Path):
  """Each paper-list entry is paired with its PDF and its DOI."""
  manifest = ingest_corpus(corpus)

  assert len(manifest.papers) == 10
  for paper in manifest.papers:
    assert paper.doi.startswith("10.")
    assert paper.pdf_path.is_file()
    assert paper.title
  assert manifest.unmatched_entries == []
  assert manifest.orphan_pdfs == []


def test_manifest_is_written_to_the_artifacts_directory(corpus: Path):
  """The stage leaves an inspectable artifact behind."""
  manifest = ingest_corpus(corpus)

  written = json.loads(
    (corpus / "artifacts" / "corpus-manifest.json").read_text(encoding="utf-8")
  )
  assert [paper["doi"] for paper in written["papers"]] == [
    paper.doi for paper in manifest.papers
  ]


def test_entries_without_a_pdf_are_reported(corpus: Path):
  """A paper-list entry whose PDF is absent is reported, not dropped silently."""
  removed = sorted((corpus / "papers").glob("*.pdf"))[0]
  removed.unlink()
  _pad_corpus(corpus, count=1)

  manifest = ingest_corpus(corpus)

  assert len(manifest.unmatched_entries) == 1
  assert manifest.unmatched_entries[0].reason == "no matching PDF"
  assert all(paper.pdf_path != removed for paper in manifest.papers)


def test_pdfs_without_an_entry_are_reported(corpus: Path):
  """A PDF nobody listed is reported as an orphan."""
  orphan = corpus / "papers" / "stray-paper.pdf"
  shutil.copy(sorted((corpus / "papers").glob("*.pdf"))[0], orphan)

  manifest = ingest_corpus(corpus)

  assert [path.name for path in manifest.orphan_pdfs] == ["stray-paper.pdf"]


def test_a_corpus_below_the_envelope_is_rejected(corpus: Path):
  """Fewer than 10 Papers is outside the supported envelope."""
  for pdf in sorted((corpus / "papers").glob("*.pdf"))[:3]:
    pdf.unlink()
  _drop_entries(corpus, count=3)

  with pytest.raises(CorpusSizeError) as error:
    ingest_corpus(corpus)

  assert "10" in str(error.value) and "75" in str(error.value)
  assert "7" in str(error.value)


def test_a_corpus_above_the_envelope_is_rejected(corpus: Path):
  """More than 75 Papers is outside the supported envelope."""
  _pad_corpus(corpus, count=70)

  with pytest.raises(CorpusSizeError) as error:
    ingest_corpus(corpus)

  assert "80" in str(error.value)


def _drop_entries(corpus: Path, count: int) -> None:
  """Remove the first `count` entries from the corpus BibTeX file."""
  bib = (corpus / "corpus.bib").read_text(encoding="utf-8")
  entries = [entry for entry in bib.split("@article{") if entry.strip()]
  kept = "".join(f"@article{{{entry}" for entry in entries[count:])
  (corpus / "corpus.bib").write_text(kept, encoding="utf-8")


def _pad_corpus(corpus: Path, count: int) -> None:
  """Add `count` synthetic Papers (entry plus PDF) to the corpus."""
  template = sorted((corpus / "papers").glob("*.pdf"))[0]
  with (corpus / "corpus.bib").open("a", encoding="utf-8") as bib:
    for index in range(count):
      doi = f"10.9999/pad.{index:03d}"
      bib.write(
        f"\n@article{{pad{index:03d},\n"
        f"  title = {{Padding paper {index}}},\n"
        f"  author = {{Pad, Ada}},\n"
        f"  year = {{2020}},\n"
        f"  doi = {{{doi}}}\n}}\n"
      )
      name = f"pad{index:03d}-{doi.replace('/', '_')}.pdf"
      shutil.copy(template, corpus / "papers" / name)


def test_cli_ingest_writes_the_manifest(corpus: Path):
  """`ingest <corpus>` succeeds and leaves the artifact on disk."""
  assert main(["ingest", str(corpus)]) == 0
  assert (corpus / "artifacts" / "corpus-manifest.json").is_file()


def test_cli_reports_a_corpus_outside_the_envelope(corpus: Path, caplog):
  """An unsupported Corpus size fails the command with an explanation."""
  for pdf in sorted((corpus / "papers").glob("*.pdf"))[:3]:
    pdf.unlink()

  assert main(["ingest", str(corpus)]) == 1
  assert "v1 supports 10-75" in caplog.text
