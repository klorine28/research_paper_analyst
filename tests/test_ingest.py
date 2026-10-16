"""Behavior of the ingest stage: paper list plus PDFs in, CorpusManifest out."""

import json
import shutil
from pathlib import Path

import pytest

from research_gap_dashboard.cli import main
from research_gap_dashboard.ingest import CorpusSizeError, check_corpus, ingest_corpus
from research_gap_dashboard.sources import WorkRecord


@pytest.fixture(name="corpus")
def corpus_fixture(cardiology_corpus: Path, tmp_path: Path) -> Path:
  """Copy the fixture Corpus so a test can add or remove files freely."""
  target = tmp_path / "corpus"
  shutil.copytree(cardiology_corpus, target)
  return target


class _StubAdapter:  # pylint: disable=too-few-public-methods
  """A source adapter returning canned WorkRecords keyed by DOI."""

  name = "stub"

  def __init__(self, records: dict[str, WorkRecord]):
    self._records = records

  def resolve(self, doi: str) -> WorkRecord | None:
    """Return the canned record for a DOI, or None when there is none."""
    return self._records.get(doi)


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


def test_resolution_enriches_papers_with_canonical_metadata(corpus: Path):
  """A resolved DOI overwrites the paper's metadata with the canonical record."""
  base = ingest_corpus(corpus)
  target = base.papers[0]
  adapter = _StubAdapter(
    {
      target.doi: WorkRecord(
        doi=target.doi,
        title="Canonical Title",
        year=1999,
        venue="Canonical Journal",
        authors=["Canon, First"],
        openalex_id="https://openalex.org/W1",
        referenced_works=["https://openalex.org/W2"],
        cited_by_count=42,
      )
    }
  )

  manifest = ingest_corpus(corpus, adapter=adapter)

  enriched = next(p for p in manifest.papers if p.doi == target.doi)
  assert enriched.title == "Canonical Title"
  assert enriched.year == 1999
  assert enriched.journal == "Canonical Journal"
  assert enriched.authors == ["Canon, First"]
  assert enriched.openalex_id == "https://openalex.org/W1"
  assert enriched.referenced_works == ["https://openalex.org/W2"]
  assert enriched.cited_by_count == 42
  assert enriched.resolution_error is None


def test_unresolved_dois_are_reported_per_paper(corpus: Path):
  """A DOI the adapter cannot resolve is flagged, and the Paper is kept."""
  adapter = _StubAdapter({})  # resolves nothing

  manifest = ingest_corpus(corpus, adapter=adapter)

  assert len(manifest.papers) == 10
  assert all(paper.resolution_error is not None for paper in manifest.papers)


def test_without_an_adapter_papers_keep_bibtex_metadata(corpus: Path):
  """Ingest resolves nothing when no adapter is supplied."""
  manifest = ingest_corpus(corpus)

  assert all(paper.resolution_error is None for paper in manifest.papers)
  assert all(paper.openalex_id == "" for paper in manifest.papers)


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


# --- Papers without a DOI (#73) -----------------------------------------------


def _add_preprint(corpus: Path, key: str, *, with_pdf: bool = True) -> None:
  """Append a no-DOI entry (a preprint) and, optionally, a PDF named by its key."""
  with (corpus / "corpus.bib").open("a", encoding="utf-8") as bib:
    bib.write(
      f"\n@misc{{{key},\n"
      f"  title = {{Unpublished work {key}}},\n"
      f"  author = {{Preprint, Pat}},\n"
      f"  year = {{2024}}\n}}\n"
    )
  if with_pdf:
    template = sorted((corpus / "papers").glob("*.pdf"))[0]
    shutil.copy(template, corpus / "papers" / f"{key}.pdf")


def test_an_entry_without_a_doi_is_paired_with_its_pdf_by_citation_key(corpus: Path):
  """A no-DOI entry with a matching PDF becomes a first-class Paper."""
  _add_preprint(corpus, "thesis2024")

  manifest = ingest_corpus(corpus)

  assert len(manifest.papers) == 11
  paper = next(p for p in manifest.papers if p.citation_key == "thesis2024")
  assert paper.doi == ""
  assert paper.pdf_path.name == "thesis2024.pdf"
  assert paper.title == "Unpublished work thesis2024"
  assert manifest.unmatched_entries == []
  assert manifest.orphan_pdfs == []


def test_an_entry_without_a_doi_or_a_pdf_is_reported_unmatched(corpus: Path):
  """A no-DOI entry whose PDF is absent is reported, not silently dropped."""
  _add_preprint(corpus, "thesis2024", with_pdf=False)

  manifest = ingest_corpus(corpus)

  assert len(manifest.papers) == 10
  assert [u.entry.citation_key for u in manifest.unmatched_entries] == ["thesis2024"]
  assert manifest.unmatched_entries[0].reason == "no matching PDF"


def test_a_paper_without_a_doi_is_not_resolved_and_carries_no_citation_graph(
  corpus: Path,
):
  """Resolution skips a no-DOI Paper: no lookup, no graph, and no false error."""
  _add_preprint(corpus, "thesis2024")
  looked_up: list[str] = []

  class _RecordingAdapter(_StubAdapter):  # pylint: disable=too-few-public-methods
    def resolve(self, doi: str) -> WorkRecord | None:
      looked_up.append(doi)
      return WorkRecord(
        doi=doi,
        openalex_id=f"https://openalex.org/{doi}",
        referenced_works=["https://openalex.org/W9"],
      )

  manifest = ingest_corpus(corpus, adapter=_RecordingAdapter({}))

  assert "" not in looked_up
  assert len(looked_up) == 10
  paper = next(p for p in manifest.papers if p.citation_key == "thesis2024")
  assert paper.openalex_id == ""
  assert paper.referenced_works == []
  assert paper.resolution_error is None
  assert paper.title == "Unpublished work thesis2024"


# --- Pairing check before ingesting (#75) -------------------------------------


def test_the_pairing_check_reports_pairs_and_leftovers_without_writing(corpus: Path):
  """`check_corpus` pairs like ingest, but leaves no manifest behind."""
  orphan = corpus / "papers" / "stray-paper.pdf"
  shutil.copy(sorted((corpus / "papers").glob("*.pdf"))[0], orphan)
  _add_preprint(corpus, "thesis2024", with_pdf=False)

  check = check_corpus(corpus)

  assert len(check.paired) == 10
  assert [u.entry.citation_key for u in check.unmatched_entries] == ["thesis2024"]
  assert [path.name for path in check.orphan_pdfs] == ["stray-paper.pdf"]
  assert check.within_envelope
  assert check.refusal is None
  assert not (corpus / "artifacts" / "corpus-manifest.json").exists()


def test_the_pairing_check_refuses_a_corpus_outside_the_envelope(corpus: Path):
  """Outside 10-75 Papers, the check says so, naming the count, before ingest."""
  for pdf in sorted((corpus / "papers").glob("*.pdf"))[:3]:
    pdf.unlink()

  check = check_corpus(corpus)

  assert not check.within_envelope
  refusal = check.refusal or ""
  assert "v1 supports 10-75" in refusal
  assert "7 Papers" in refusal


def test_cli_ingest_check_prints_the_pairing_as_json(
  corpus: Path, capsys: pytest.CaptureFixture[str]
):
  """`ingest --check` prints the pairing on stdout and writes no manifest."""
  assert main(["ingest", str(corpus), "--check"]) == 0

  printed = json.loads(capsys.readouterr().out)
  assert len(printed["paired"]) == 10
  assert printed["within_envelope"] is True
  assert not (corpus / "artifacts" / "corpus-manifest.json").exists()


def test_cli_ingest_check_fails_outside_the_envelope_but_still_reports(
  corpus: Path, capsys: pytest.CaptureFixture[str]
):
  """A Corpus outside the envelope fails the check, with the pairing printed."""
  for pdf in sorted((corpus / "papers").glob("*.pdf"))[:3]:
    pdf.unlink()

  assert main(["ingest", str(corpus), "--check"]) == 1

  printed = json.loads(capsys.readouterr().out)
  assert printed["within_envelope"] is False
  assert len(printed["unmatched_entries"]) == 3
