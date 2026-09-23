"""Behavior of the corpus directory convention every pipeline stage relies on."""

from pathlib import Path

from research_gap_dashboard.corpus_layout import inspect_corpus_layout


def make_valid_corpus(root: Path) -> Path:
  """Write the documented layout with two PDFs and a bibliography."""
  (root / "papers").mkdir()
  (root / "paper-data").mkdir()
  (root / "artifacts").mkdir()
  (root / "judgments").mkdir()
  (root / "corpus.bib").write_text(
    "@article{a2020, doi = {10.1/a}}\n", encoding="utf-8"
  )
  (root / "papers" / "a2020.pdf").write_bytes(b"%PDF-1.4\n")
  (root / "papers" / "b2021.pdf").write_bytes(b"%PDF-1.4\n")
  return root


def test_valid_layout_has_no_problems(tmp_path: Path):
  """A corpus following the convention is reported as valid."""
  report = inspect_corpus_layout(make_valid_corpus(tmp_path))

  assert report.is_valid
  assert report.problems == []


def test_missing_directories_are_reported(tmp_path: Path):
  """Each convention directory that is absent is named as a missing piece."""
  make_valid_corpus(tmp_path)
  (tmp_path / "judgments").rmdir()
  (tmp_path / "artifacts").rmdir()

  report = inspect_corpus_layout(tmp_path)

  assert not report.is_valid
  assert {(p.kind, p.path) for p in report.problems} == {
    ("missing", tmp_path / "judgments"),
    ("missing", tmp_path / "artifacts"),
  }


def test_missing_paper_list_is_reported(tmp_path: Path):
  """A corpus with no BibTeX or DOI list at its root is incomplete."""
  make_valid_corpus(tmp_path)
  (tmp_path / "corpus.bib").unlink()

  report = inspect_corpus_layout(tmp_path)

  assert not report.is_valid
  assert [p.kind for p in report.problems] == ["missing"]
  assert report.paper_list_path is None


def test_paper_list_may_be_a_doi_list(tmp_path: Path):
  """A plain DOI list at the root serves as the paper list."""
  make_valid_corpus(tmp_path)
  (tmp_path / "corpus.bib").unlink()
  (tmp_path / "dois.txt").write_text("10.1/a\n", encoding="utf-8")

  report = inspect_corpus_layout(tmp_path)

  assert report.is_valid
  assert report.paper_list_path == tmp_path / "dois.txt"


def test_pdfs_outside_the_papers_directory_are_reported_as_misplaced(tmp_path: Path):
  """PDFs belong in papers/; one dropped at the root is flagged, not ignored."""
  make_valid_corpus(tmp_path)
  (tmp_path / "c2022.pdf").write_bytes(b"%PDF-1.4\n")

  report = inspect_corpus_layout(tmp_path)

  assert not report.is_valid
  assert [(p.kind, p.path) for p in report.problems] == [
    ("misplaced", tmp_path / "c2022.pdf")
  ]


def test_non_pdf_files_in_the_papers_directory_are_reported_as_misplaced(
  tmp_path: Path,
):
  """papers/ holds only PDFs, so derived data landing there is flagged."""
  make_valid_corpus(tmp_path)
  (tmp_path / "papers" / "a2020.sections.json").write_text("{}", encoding="utf-8")

  report = inspect_corpus_layout(tmp_path)

  assert not report.is_valid
  assert [(p.kind, p.path) for p in report.problems] == [
    ("misplaced", tmp_path / "papers" / "a2020.sections.json")
  ]


def test_hidden_files_are_ignored(tmp_path: Path):
  """Housekeeping files (.gitkeep, .DS_Store) are not corpus content."""
  make_valid_corpus(tmp_path)
  (tmp_path / "papers" / ".gitkeep").touch()
  (tmp_path / ".DS_Store").touch()

  report = inspect_corpus_layout(tmp_path)

  assert report.is_valid


def test_pdf_paths_are_listed_for_a_valid_corpus(tmp_path: Path):
  """The report lists the Papers' PDFs so later stages need not re-scan."""
  report = inspect_corpus_layout(make_valid_corpus(tmp_path))

  assert report.pdf_paths == [
    tmp_path / "papers" / "a2020.pdf",
    tmp_path / "papers" / "b2021.pdf",
  ]


def test_a_directory_that_does_not_exist_is_reported(tmp_path: Path):
  """Pointing the tool at a non-existent corpus fails loudly."""
  report = inspect_corpus_layout(tmp_path / "nope")

  assert not report.is_valid
  assert report.problems[0].path == tmp_path / "nope"
