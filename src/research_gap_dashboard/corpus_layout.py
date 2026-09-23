"""
The on-disk convention for a Corpus directory, and the check that it is followed.

This module knows where things live; it never reads a Paper's content. Parsing
the bibliography, PDFs, or artifacts belongs to the pipeline stages.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

PAPERS_DIR = "papers"
PAPER_DATA_DIR = "paper-data"
ARTIFACTS_DIR = "artifacts"
JUDGMENTS_DIR = "judgments"
DOI_LIST_NAME = "dois.txt"
BIBTEX_SUFFIX = ".bib"
PDF_SUFFIX = ".pdf"


class CorpusLayout(BaseModel):
  """The paths that make up a Corpus directory."""

  root: Path

  @property
  def papers_dir(self) -> Path:
    """Directory holding only the Papers' PDF files."""
    return self.root / PAPERS_DIR

  @property
  def paper_data_dir(self) -> Path:
    """Directory holding per-Paper derived data (parsed sections, Extractions)."""
    return self.root / PAPER_DATA_DIR

  @property
  def artifacts_dir(self) -> Path:
    """Directory holding stage output artifacts."""
    return self.root / ARTIFACTS_DIR

  @property
  def judgments_dir(self) -> Path:
    """Directory holding accept/reject judgments and chat history."""
    return self.root / JUDGMENTS_DIR


class CorpusLayoutProblem(BaseModel):
  """One departure from the corpus directory convention."""

  kind: Literal["missing", "misplaced"]
  path: Path
  message: str


class CorpusLayoutReport(BaseModel):
  """What a corpus directory contains, and how it departs from the convention."""

  layout: CorpusLayout
  problems: list[CorpusLayoutProblem]
  paper_list_path: Path | None
  pdf_paths: list[Path]

  @property
  def is_valid(self) -> bool:
    """True when the directory follows the convention."""
    return not self.problems


def inspect_corpus_layout(root: Path) -> CorpusLayoutReport:
  """Report what is present, missing, or misplaced in a corpus directory."""
  layout = CorpusLayout(root=root)
  problems: list[CorpusLayoutProblem] = []

  if not root.is_dir():
    return CorpusLayoutReport(
      layout=layout,
      problems=[
        CorpusLayoutProblem(
          kind="missing",
          path=root,
          message=f"Corpus directory '{root}' does not exist.",
        )
      ],
      paper_list_path=None,
      pdf_paths=[],
    )

  for directory in (
    layout.papers_dir,
    layout.paper_data_dir,
    layout.artifacts_dir,
    layout.judgments_dir,
  ):
    if not directory.is_dir():
      problems.append(
        CorpusLayoutProblem(
          kind="missing",
          path=directory,
          message=f"Expected directory '{directory.name}/' in the corpus directory.",
        )
      )

  problems.extend(_misplaced_files(layout))

  paper_list_path = _find_paper_list(root)
  if paper_list_path is None:
    problems.append(
      CorpusLayoutProblem(
        kind="missing",
        path=root,
        message=(
          f"Expected a BibTeX file ('*{BIBTEX_SUFFIX}') or a DOI list "
          f"('{DOI_LIST_NAME}') at the corpus root."
        ),
      )
    )

  return CorpusLayoutReport(
    layout=layout,
    problems=problems,
    paper_list_path=paper_list_path,
    pdf_paths=_pdf_paths(layout),
  )


def _pdf_paths(layout: CorpusLayout) -> list[Path]:
  """Return the Papers' PDFs, in a stable order."""
  if not layout.papers_dir.is_dir():
    return []
  return sorted(p for p in layout.papers_dir.iterdir() if p.is_file() and _is_pdf(p))


def _misplaced_files(layout: CorpusLayout) -> list[CorpusLayoutProblem]:
  """Report PDFs outside papers/ and non-PDF files inside it."""
  problems: list[CorpusLayoutProblem] = []

  for path in sorted(layout.root.iterdir()):
    if _is_hidden(path):
      continue
    if path.is_file() and _is_pdf(path):
      problems.append(
        CorpusLayoutProblem(
          kind="misplaced",
          path=path,
          message=f"PDF '{path.name}' belongs in '{PAPERS_DIR}/'.",
        )
      )

  if layout.papers_dir.is_dir():
    for path in sorted(layout.papers_dir.iterdir()):
      if _is_hidden(path):
        continue
      if not _is_pdf(path):
        problems.append(
          CorpusLayoutProblem(
            kind="misplaced",
            path=path,
            message=(
              f"'{PAPERS_DIR}/' holds only PDFs; derived data belongs in "
              f"'{PAPER_DATA_DIR}/'."
            ),
          )
        )

  return problems


def _is_hidden(path: Path) -> bool:
  """Report whether the path is housekeeping (.gitkeep, .DS_Store), not content."""
  return path.name.startswith(".")


def _is_pdf(path: Path) -> bool:
  """Report whether the path is a PDF file, matching the suffix case-insensitively."""
  return path.is_file() and path.suffix.lower() == PDF_SUFFIX


def _find_paper_list(root: Path) -> Path | None:
  """Return the BibTeX file or DOI list at the corpus root, if there is one."""
  bibtex_files = sorted(p for p in root.glob(f"*{BIBTEX_SUFFIX}") if p.is_file())
  if bibtex_files:
    return bibtex_files[0]
  doi_list = root / DOI_LIST_NAME
  return doi_list if doi_list.is_file() else None
