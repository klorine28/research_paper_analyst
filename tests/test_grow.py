"""
The Grow path: adding a suggested Paper to a Corpus (ADR 0009 §11-12, #91).

A Retrieval Gap or a saturation hit is added by fetching its open-access PDF,
else from an upload, else it is marked unavailable. The seam is
`grow.add_suggested_paper` / `grow.mark_unavailable`, with a fake OpenAlex and
a fake download so nothing touches the network; the Corpus is a temporary one
of ten paired Papers, and ingest's own pairing check is the judge of what is in
the Corpus.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from research_gap_dashboard import cli
from research_gap_dashboard.dashboard.entry_log import EntryRoute, load_entry_log
from research_gap_dashboard.grow import (
  GrowError,
  WorkRef,
  add_suggested_paper,
  mark_unavailable,
  read_suggested_papers,
)
from research_gap_dashboard.ingest import MAX_PAPERS, check_corpus
from research_gap_dashboard.sources import OpenAlexAdapter

_PDF = b"%PDF-1.7 a suggested paper"
_NOW = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
_DOI = "10.5555/open.1"


def _make_corpus(root: Path, count: int = 10, *, doi_list: bool = False) -> Path:
  """Write a Corpus of `count` paired Papers (BibTeX or DOI list)."""
  (root / "papers").mkdir(parents=True)
  if doi_list:
    dois = [f"10.1/p{index}" for index in range(count)]
    (root / "dois.txt").write_text("\n".join(dois) + "\n", encoding="utf-8")
    for doi in dois:
      (root / "papers" / f"{doi.replace('/', '_')}.pdf").write_bytes(_PDF)
    return root
  entries = [
    f"@article{{paper{index:02d},\n  title = {{Paper {index}}},\n"
    f"  doi = {{10.1/p{index}}}\n}}"
    for index in range(count)
  ]
  (root / "corpus.bib").write_text("\n\n".join(entries) + "\n", encoding="utf-8")
  for index in range(count):
    (root / "papers" / f"paper{index:02d}.pdf").write_bytes(_PDF)
  return root


_OA_WORK = {
  "id": "https://openalex.org/W42",
  "doi": f"https://doi.org/{_DOI}",
  "title": "Heart failure in {older} adults",
  "publication_year": 2021,
  "authorships": [
    {"author": {"display_name": "Ada Lovelace"}},
    {"author": {"display_name": "Alan Turing"}},
  ],
  "primary_location": {"source": {"display_name": "Open Journal"}},
}


class _FakeOpenAlex(OpenAlexAdapter):
  """The real adapter over a fake fetch: a canned work, or a dead network."""

  def __init__(self, pdf_urls: list[str] | None, error: Exception | None = None):
    super().__init__(fetch=self._answer)
    self.pdf_urls = pdf_urls
    self.error = error
    self.urls: list[str] = []

  def _answer(self, url: str) -> dict[str, Any] | None:
    self.urls.append(url)
    if self.error is not None:
      raise self.error
    if self.pdf_urls is None:
      return None
    return {
      **_OA_WORK,
      "locations": [{"is_oa": True, "pdf_url": pdf} for pdf in self.pdf_urls],
    }

  @property
  def refs(self) -> list[str]:
    """The work references looked up, as the URL path names them."""
    return [url.split("/works/", 1)[1].split("?", 1)[0] for url in self.urls]


def _downloads(bodies: dict[str, bytes | None]):
  def download(url: str) -> bytes | None:
    return bodies.get(url)

  return download


@pytest.fixture(name="corpus")
def corpus_fixture(tmp_path: Path) -> Path:
  """Return a Corpus of ten paired Papers listed in BibTeX."""
  return _make_corpus(tmp_path / "corpus")


def test_open_access_pdf_is_fetched_placed_and_listed(corpus: Path):
  """An open-access suggestion becomes a paired Paper, logged with route and date."""
  openalex = _FakeOpenAlex(["https://x/landing", "https://x/paper.pdf"])
  download = _downloads({"https://x/landing": b"<html>", "https://x/paper.pdf": _PDF})

  outcome = add_suggested_paper(
    corpus,
    WorkRef(doi=_DOI.upper()),
    "retrieval",
    openalex=openalex,
    download=download,
    now=_NOW,
  )

  assert outcome.status == "added"
  assert openalex.refs == [f"doi:{_DOI}"]
  check = check_corpus(corpus)
  assert len(check.paired) == 11
  assert outcome.paper is not None
  assert outcome.paper.citation_key in check.paired
  assert (corpus / "papers" / outcome.paper.pdf_name).read_bytes() == _PDF
  assert "Heart failure in older adults" in (corpus / "corpus.bib").read_text()
  record = read_suggested_papers(corpus).papers[0]
  assert record.status == "added"
  assert record.routes == ["retrieval"]
  assert record.pdf_source == "open_access"
  assert record.pdf_url == "https://x/paper.pdf"
  assert record.history[-1].at == _NOW


def test_no_open_access_pdf_leaves_a_pending_paper_outside_the_corpus(corpus: Path):
  """Without a PDF nothing is listed: a pending Paper is in no denominator."""
  bib_before = (corpus / "corpus.bib").read_text()

  outcome = add_suggested_paper(
    corpus,
    WorkRef(doi=_DOI),
    "saturation",
    openalex=_FakeOpenAlex([]),
    download=_downloads({}),
    now=_NOW,
  )

  assert outcome.status == "pending"
  assert (corpus / "corpus.bib").read_text() == bib_before
  assert sorted(p.name for p in (corpus / "papers").iterdir()) == sorted(
    f"paper{index:02d}.pdf" for index in range(10)
  )
  assert len(check_corpus(corpus).paired) == 10
  record = read_suggested_papers(corpus).papers[0]
  assert record.status == "pending"
  assert record.title == "Heart failure in {older} adults"


def test_offline_lookup_is_pending_not_a_failure(corpus: Path):
  """An unreachable OpenAlex leaves the paper pending, with the reason."""
  outcome = add_suggested_paper(
    corpus,
    WorkRef(doi=_DOI),
    "retrieval",
    openalex=_FakeOpenAlex([], error=httpx.ConnectError("offline")),
    download=_downloads({}),
    title="A title from the suggestion",
    now=_NOW,
  )

  assert outcome.status == "pending"
  assert "offline" in outcome.message
  assert read_suggested_papers(corpus).papers[0].title == "A title from the suggestion"


def test_uploaded_pdf_adds_a_pending_paper(corpus: Path):
  """Uploading the PDF of a pending paper adds it, one record, both routes."""
  for route in ("saturation", "retrieval"):
    add_suggested_paper(
      corpus,
      WorkRef(doi=_DOI),
      route,
      openalex=_FakeOpenAlex([]),
      download=_downloads({}),
      now=_NOW,
    )

  outcome = add_suggested_paper(
    corpus,
    WorkRef(openalex_id="https://openalex.org/W42", doi=_DOI),
    "retrieval",
    openalex=_FakeOpenAlex([]),
    download=_downloads({}),
    upload=_PDF,
    now=_NOW,
  )

  assert outcome.status == "added"
  ledger = read_suggested_papers(corpus)
  assert len(ledger.papers) == 1
  assert ledger.papers[0].routes == ["saturation", "retrieval"]
  assert ledger.papers[0].pdf_source == "upload"
  assert len(check_corpus(corpus).paired) == 11


def test_an_upload_that_is_not_a_pdf_is_refused(corpus: Path):
  """Only a real PDF can carry Evidence, so nothing is added from anything else."""
  with pytest.raises(GrowError, match="not a PDF"):
    add_suggested_paper(
      corpus,
      WorkRef(doi=_DOI),
      "retrieval",
      openalex=_FakeOpenAlex([]),
      download=_downloads({}),
      upload=b"<html>",
      now=_NOW,
    )
  assert len(check_corpus(corpus).paired) == 10


def test_a_paper_already_in_the_corpus_is_never_added_twice(corpus: Path):
  """One Grow path: a second add of the same work, by any route, is refused."""
  openalex = _FakeOpenAlex(["https://x/paper.pdf"])
  download = _downloads({"https://x/paper.pdf": _PDF})
  add_suggested_paper(
    corpus, WorkRef(doi=_DOI), "retrieval", openalex=openalex, download=download
  )

  again = add_suggested_paper(
    corpus,
    WorkRef(openalex_id="W42"),
    "saturation",
    openalex=openalex,
    download=download,
  )
  listed = add_suggested_paper(
    corpus, WorkRef(doi="10.1/p3"), "saturation", openalex=openalex, download=download
  )

  assert again.status == "already_in_corpus"
  assert listed.status == "already_in_corpus"
  assert len(check_corpus(corpus).paired) == 11


def test_a_full_corpus_refuses_another_paper(tmp_path: Path):
  """The 10-75 envelope is checked before anything is placed (ADR 0010 §5)."""
  corpus = _make_corpus(tmp_path / "full", MAX_PAPERS)

  with pytest.raises(GrowError, match=str(MAX_PAPERS)):
    add_suggested_paper(
      corpus,
      WorkRef(doi=_DOI),
      "retrieval",
      openalex=_FakeOpenAlex(["https://x/paper.pdf"]),
      download=_downloads({"https://x/paper.pdf": _PDF}),
    )
  assert not (corpus / "suggested_papers.json").exists()


def test_a_doi_list_corpus_gets_a_doi_line_and_a_matching_pdf(tmp_path: Path):
  """A Corpus listed by DOI grows by DOI, and its new PDF pairs by DOI."""
  corpus = _make_corpus(tmp_path / "dois", doi_list=True)

  outcome = add_suggested_paper(
    corpus,
    WorkRef(doi=_DOI),
    "retrieval",
    openalex=_FakeOpenAlex(["https://x/paper.pdf"]),
    download=_downloads({"https://x/paper.pdf": _PDF}),
  )

  assert outcome.status == "added"
  assert (corpus / "dois.txt").read_text().splitlines()[-1] == _DOI
  assert _DOI in check_corpus(corpus).paired


def test_mark_unavailable_records_reason_and_date_and_upload_reverses_it(
  corpus: Path,
):
  """Unavailable is recorded with its reason and date, and undone by an upload."""
  record = mark_unavailable(
    corpus,
    WorkRef(doi=_DOI),
    "saturation",
    "Library has no access",
    title="Closed paper",
    now=_NOW,
  )

  assert record.reason == "Library has no access"
  assert record.marked_at == _NOW
  assert load_entry_log(corpus).unavailable == [record]
  assert read_suggested_papers(corpus).papers == []
  assert len(check_corpus(corpus).paired) == 10

  outcome = add_suggested_paper(
    corpus,
    WorkRef(doi=_DOI),
    "saturation",
    openalex=_FakeOpenAlex([]),
    download=_downloads({}),
    upload=_PDF,
  )

  assert outcome.status == "added"
  assert load_entry_log(corpus).unavailable == []
  assert [entry.route for entry in load_entry_log(corpus).entries] == [
    EntryRoute.SATURATION_HIT
  ]


def test_mark_unavailable_needs_a_reason(corpus: Path):
  """A blank reason is refused: the scope must say why a paper is missing."""
  with pytest.raises(GrowError, match="reason"):
    mark_unavailable(corpus, WorkRef(doi=_DOI), "retrieval", "  ")


def test_cli_prints_the_outcome_and_exits_incomplete_when_pending(
  corpus: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
  """`add-suggested` prints its outcome as JSON; pending exits 2, added exits 0."""
  monkeypatch.setattr(cli, "OpenAlexAdapter", lambda **_: _FakeOpenAlex([]))
  upload = corpus.parent / "upload.pdf"
  upload.write_bytes(_PDF)

  pending = cli.main(
    ["add-suggested", str(corpus), "--doi", _DOI, "--route", "retrieval"]
  )
  pending_out = json.loads(capsys.readouterr().out)
  added = cli.main(
    [
      "add-suggested",
      str(corpus),
      "--doi",
      _DOI,
      "--route",
      "retrieval",
      "--upload",
      str(upload),
    ]
  )
  added_out = json.loads(capsys.readouterr().out)

  assert (pending, pending_out["status"]) == (cli.EXIT_INCOMPLETE, "pending")
  assert (added, added_out["status"]) == (cli.EXIT_OK, "added")


def test_cli_marks_a_paper_unavailable(corpus: Path):
  """`mark-unavailable` records the reason without touching the network."""
  code = cli.main(
    [
      "mark-unavailable",
      str(corpus),
      "--doi",
      _DOI,
      "--route",
      "saturation",
      "--reason",
      "Not held by the library",
    ]
  )

  assert code == cli.EXIT_OK
  assert load_entry_log(corpus).unavailable[0].reason == "Not held by the library"
