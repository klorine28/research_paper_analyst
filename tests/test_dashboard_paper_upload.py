"""
Uploading Papers in the app and ingesting them (#75).

The seam is `dashboard.paper_upload` (place PDFs, place or extend the paper
list, ask the pipeline for its pairing check) plus the Add Papers panel, driven
through `AppTest`. The runner's subprocess is patched: the fake hands `ingest`
commands to the CLI in-process (offline and keyless), so the pairing and the
manifest are the pipeline's own, and records every command it was asked to run.
"""

import contextlib
import io
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard import cli
from research_gap_dashboard.dashboard import pipeline_runner, text
from research_gap_dashboard.dashboard.corpus_scope import create_corpus
from research_gap_dashboard.dashboard.paper_upload import (
  UploadError,
  check_pairing,
  place_paper_list,
  place_pdfs,
)

_PDF = b"%PDF-1.4 not a real paper"


class _InProcessCli:
  """Stands in for `subprocess.run`: runs `ingest` in-process, records commands."""

  def __init__(self) -> None:
    self.calls: list[list[str]] = []

  def __call__(self, args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
    self.calls.append(list(args))
    assert args[:4] == ["uv", "run", "research-gap-dashboard", "ingest"], args
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
      code = cli.main(args[3:])
    return subprocess.CompletedProcess(args, code, out.getvalue(), "")

  def stages(self) -> list[list[str]]:
    """Each launched command's stage and options, without the Corpus path."""
    return [[call[3], *call[5:]] for call in self.calls]


@pytest.fixture(name="fake_cli")
def fake_cli_fixture(monkeypatch: pytest.MonkeyPatch) -> _InProcessCli:
  """Patch the subprocess the runner launches with the in-process CLI."""
  fake = _InProcessCli()
  monkeypatch.setattr(pipeline_runner.subprocess, "run", fake)
  return fake


@pytest.fixture(name="corpus")
def corpus_fixture(tmp_path: Path) -> Path:
  """Create a Corpus as the app does: its layout and scope, no Papers."""
  return create_corpus(
    tmp_path / "corpora", name="review", question="Q", field="F", searched="S"
  ).root


def _bib(keys: list[str], *, with_doi: bool = True) -> bytes:
  """Return a BibTeX paper list, one entry per key (DOI 10.1/<key> if wanted)."""
  entries = []
  for key in keys:
    doi = f",\n  doi = {{10.1/{key}}}" if with_doi else ""
    entries.append(f"@article{{{key},\n  title = {{Paper {key}}}{doi}\n}}")
  return "\n\n".join(entries).encode("utf-8")


def _pdfs(keys: list[str]) -> list[tuple[str, bytes]]:
  return [(f"{key}.pdf", _PDF) for key in keys]


def _keys(count: int, prefix: str = "paper") -> list[str]:
  return [f"{prefix}{index:02d}" for index in range(count)]


# -- Placing PDFs -- #


def test_uploaded_pdfs_land_in_papers(corpus: Path):
  """Each uploaded PDF is written into the Corpus's papers/ folder."""
  placement = place_pdfs(corpus, _pdfs(["smith2020", "lee2021"]))

  assert sorted(placement.added) == ["lee2021.pdf", "smith2020.pdf"]
  assert (corpus / "papers" / "smith2020.pdf").read_bytes() == _PDF
  assert not (corpus / "artifacts" / "corpus-manifest.json").exists()


@pytest.mark.parametrize(
  ("name", "content"),
  [("notes.txt", _PDF), ("fake.pdf", b"hello"), (".hidden.pdf", _PDF)],
)
def test_anything_but_a_pdf_is_refused(corpus: Path, name: str, content: bytes):
  """papers/ holds only PDFs, so other files are refused with a reason."""
  placement = place_pdfs(corpus, [(name, content)])

  assert not placement.added
  assert [refused.name for refused in placement.refused] == [name]
  assert not list((corpus / "papers").iterdir())


def test_an_upload_never_lands_outside_papers(corpus: Path):
  """Only the file name of an upload is kept."""
  place_pdfs(corpus, [("../../escape.pdf", _PDF)])

  assert (corpus / "papers" / "escape.pdf").is_file()
  assert not (corpus.parent / "escape.pdf").exists()


def test_a_pdf_with_a_name_already_uploaded_replaces_it(corpus: Path):
  """Re-uploading a PDF (a corrected copy) replaces the old one, and says so."""
  place_pdfs(corpus, _pdfs(["smith2020"]))

  placement = place_pdfs(corpus, [("smith2020.pdf", _PDF + b" v2")])

  assert placement.replaced == ["smith2020.pdf"]
  assert (corpus / "papers" / "smith2020.pdf").read_bytes() == _PDF + b" v2"


# -- Placing the paper list -- #


def test_a_first_bibtex_upload_becomes_the_paper_list(corpus: Path):
  """With no paper list yet, an uploaded .bib becomes corpus.bib."""
  placement = place_paper_list(corpus, "export.bib", _bib(["a", "b"]))

  assert placement.path == corpus / "corpus.bib"
  assert placement.added == 2
  assert "@article{a," in (corpus / "corpus.bib").read_text(encoding="utf-8")


def test_a_bibtex_upload_is_appended_skipping_entries_already_listed(corpus: Path):
  """A second .bib adds its new entries; a repeated key or DOI is skipped."""
  place_paper_list(corpus, "first.bib", _bib(["a", "b"]))
  same_doi = b"@article{renamed,\n  doi = {10.1/B}\n}"

  placement = place_paper_list(
    corpus, "second.bib", _bib(["b", "c"]) + b"\n\n" + same_doi
  )

  assert placement.added == 1
  assert placement.already_listed == ["b", "renamed"]
  listed = (corpus / "corpus.bib").read_text(encoding="utf-8")
  assert listed.count("@article{b,") == 1
  assert "@article{c," in listed


def test_a_doi_list_becomes_or_extends_dois_txt(corpus: Path):
  """A .txt DOI list becomes dois.txt, and later lists add only new DOIs."""
  place_paper_list(corpus, "dois.txt", b"10.1/a\n# a comment\n10.1/b\n")

  placement = place_paper_list(corpus, "more.txt", b"10.1/B\n10.1/c\n")

  assert placement.path == corpus / "dois.txt"
  assert placement.added == 1
  assert placement.already_listed == ["10.1/B"]
  lines = (corpus / "dois.txt").read_text(encoding="utf-8").splitlines()
  assert [line for line in lines if not line.startswith("#")] == [
    "10.1/a",
    "10.1/b",
    "10.1/c",
  ]


@pytest.mark.parametrize(
  ("existing", "upload", "content"),
  [
    (None, "list.csv", b"10.1/a"),
    (None, "empty.bib", b"% nothing here"),
    (None, "broken.bib", b"@article{a, title = {X}}\n@article{a, title = {Y}}"),
    (None, "empty.txt", b"\n# only a comment\n"),
    (("dois.txt", b"10.1/a\n"), "more.bib", b"@article{b, doi = {10.1/b}}"),
    (("corpus.bib", b"@article{a, doi = {10.1/a}}\n"), "more.txt", b"10.1/b\n"),
  ],
)
def test_a_paper_list_that_cannot_be_placed_is_refused_and_nothing_changes(
  corpus: Path, existing: tuple[str, bytes] | None, upload: str, content: bytes
):
  """Unknown formats, empty or unreadable lists, and the other kind are refused."""
  if existing is not None:
    (corpus / existing[0]).write_bytes(existing[1])
  before = {path.name: path.read_bytes() for path in corpus.iterdir() if path.is_file()}

  with pytest.raises(UploadError):
    place_paper_list(corpus, upload, content)

  after = {path.name: path.read_bytes() for path in corpus.iterdir() if path.is_file()}
  assert after == before


def test_an_entry_without_a_doi_can_be_uploaded(corpus: Path):
  """A no-DOI entry (a preprint, a thesis) is a first-class paper-list entry."""
  placement = place_paper_list(
    corpus, "preprints.bib", _bib(["thesis"], with_doi=False)
  )

  assert placement.added == 1


# -- The pairing check -- #


def test_the_pairing_check_asks_the_pipeline_and_writes_no_manifest(
  corpus: Path, fake_cli: _InProcessCli
):
  """`check_pairing` runs `ingest --check` and reads back pairs and leftovers."""
  keys = _keys(10)
  place_paper_list(corpus, "list.bib", _bib([*keys, "missing"]))
  place_pdfs(corpus, _pdfs([*keys, "stray"]))

  result = check_pairing(corpus)

  assert fake_cli.calls == [
    [*pipeline_runner.CLI_COMMAND, "ingest", str(corpus.resolve()), "--check"]
  ]
  assert result.check is not None
  assert sorted(result.check.paired) == keys
  assert [u.entry.citation_key for u in result.check.unmatched_entries] == ["missing"]
  assert [path.name for path in result.check.orphan_pdfs] == ["stray.pdf"]
  assert result.check.within_envelope
  assert not (corpus / "artifacts" / "corpus-manifest.json").exists()


def test_the_pairing_check_reports_a_corpus_outside_the_envelope(
  corpus: Path, fake_cli: _InProcessCli
):
  """Too few Papers still yields the pairing, marked outside the envelope."""
  place_paper_list(corpus, "list.bib", _bib(_keys(3)))
  place_pdfs(corpus, _pdfs(_keys(3)))

  result = check_pairing(corpus)

  assert len(fake_cli.calls) == 1
  assert result.check is not None
  assert not result.check.within_envelope
  assert len(result.check.paired) == 3


def test_a_pairing_check_with_no_paper_list_carries_its_failure(
  corpus: Path, fake_cli: _InProcessCli
):
  """With PDFs but no paper list, the check fails and says why."""
  place_pdfs(corpus, _pdfs(["a"]))

  result = check_pairing(corpus)

  assert len(fake_cli.calls) == 1
  assert result.check is None
  assert result.run.outcome == pipeline_runner.RunOutcome.FAILED


# -- The Add Papers panel -- #


def _open(corpora_dir: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpora_dir))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  return AppTest.from_file(str(app_path), default_timeout=30).run()


def _upload(
  app: AppTest, pdfs: list[tuple[str, bytes]], paper_list: tuple[str, bytes] | None
) -> AppTest:
  """Choose files in the Add Papers form and submit it."""
  by_label = {uploader.label: uploader for uploader in app.file_uploader}
  if pdfs:
    by_label[text.UPLOAD_PDFS_LABEL].set_value(
      [(name, content, "application/pdf") for name, content in pdfs]
    )
  if paper_list is not None:
    by_label[text.UPLOAD_PAPER_LIST_LABEL].set_value(
      (paper_list[0], paper_list[1], "text/plain")
    )
  submit = next(b for b in app.button if b.label == text.UPLOAD_SUBMIT)
  return submit.click().run()


def _click(app: AppTest, label: str) -> AppTest:
  return next(b for b in app.button if b.label == label).click().run()


def _shown(app: AppTest) -> str:
  blocks = [*app.success, *app.warning, *app.error, *app.info, *app.markdown]
  return " ".join(str(block.value) for block in blocks)


def test_uploading_shows_the_pairing_problems_straight_away(
  corpus: Path, monkeypatch: pytest.MonkeyPatch, fake_cli: _InProcessCli
):
  """After an upload, unmatched entries and orphan PDFs are on screen to fix."""
  app = _open(corpus.parent, monkeypatch)
  assert not app.exception
  keys = _keys(10)

  app = _upload(
    app,
    _pdfs([*keys, "stray"]),
    ("export.bib", _bib([*keys, "lost2019"])),
  )

  assert not app.exception
  assert (corpus / "papers" / "stray.pdf").is_file()
  assert (corpus / "corpus.bib").is_file()
  assert fake_cli.stages() == [["ingest", "--check"]]
  shown = _shown(app)
  assert "lost2019" in shown
  assert "stray.pdf" in shown
  assert text.PAIRING_PAIRED.format(count=10) in shown
  assert not (corpus / "artifacts" / "corpus-manifest.json").exists()


def test_ingest_runs_after_the_check_and_opens_the_corpus(
  corpus: Path, monkeypatch: pytest.MonkeyPatch, fake_cli: _InProcessCli
):
  """Within the envelope, Ingest checks again, then runs ingest; the manifest is its."""
  keys = _keys(10)
  app = _upload(_open(corpus.parent, monkeypatch), _pdfs(keys), ("l.bib", _bib(keys)))

  app = _click(app, text.INGEST_UPLOADED_BUTTON)

  assert not app.exception
  assert fake_cli.stages() == [["ingest", "--check"], ["ingest", "--check"], ["ingest"]]
  manifest = json.loads(
    (corpus / "artifacts" / "corpus-manifest.json").read_text(encoding="utf-8")
  )
  assert len(manifest["papers"]) == 10
  assert text.PAGE_OVERVIEW in [header.value for header in app.header]


def test_a_corpus_outside_the_envelope_is_refused_before_ingest(
  corpus: Path, monkeypatch: pytest.MonkeyPatch, fake_cli: _InProcessCli
):
  """Too few Papers: a clear refusal, and Ingest never launches `ingest`."""
  keys = _keys(3)
  app = _upload(_open(corpus.parent, monkeypatch), _pdfs(keys), ("l.bib", _bib(keys)))
  refusal = text.PAIRING_REFUSED.split(".", maxsplit=1)[0]
  assert refusal in _shown(app)

  app = _click(app, text.INGEST_UPLOADED_BUTTON)

  assert not app.exception
  assert ["ingest"] not in fake_cli.stages()
  assert refusal in _shown(app)
  assert not (corpus / "artifacts" / "corpus-manifest.json").exists()


def test_a_refused_paper_list_is_explained_in_the_app(
  corpus: Path, monkeypatch: pytest.MonkeyPatch, fake_cli: _InProcessCli
):
  """An unreadable paper list shows its reason instead of crashing."""
  broken = b"@article{a, title = {X}}\n@article{a, title = {Y}}"
  app = _upload(_open(corpus.parent, monkeypatch), [], ("list.bib", broken))

  assert not app.exception
  assert any("could not be read" in str(error.value) for error in app.error)
  assert not (corpus / "corpus.bib").exists()
  assert len(fake_cli.calls) == 1


def test_papers_can_be_added_to_an_ingested_corpus_from_the_pipeline_page(
  corpus: Path, monkeypatch: pytest.MonkeyPatch, fake_cli: _InProcessCli
):
  """An ingested Corpus offers Add Papers on its Pipeline page."""
  keys = _keys(10)
  place_paper_list(corpus, "l.bib", _bib(keys))
  place_pdfs(corpus, _pdfs(keys))
  assert cli.main(["ingest", str(corpus)]) == 0
  app = _open(corpus.parent, monkeypatch)
  app.sidebar.radio[0].set_value(text.PAGE_PIPELINE).run()

  app = _upload(app, _pdfs(["extra"]), ("more.bib", _bib(["extra"])))

  assert not app.exception
  assert (corpus / "papers" / "extra.pdf").is_file()
  assert fake_cli.stages() == [["ingest", "--check"]]
  assert text.PAIRING_PAIRED.format(count=11) in _shown(app)
