"""
The Corpus entry log and the Overview timeline (#92, ADR 0010 §4).

The seam is `dashboard.entry_log` (record, mark unavailable, reconcile against
the manifest, and the joined view) plus where the dashboard writes to it (the
paper-list upload) and shows it (the Overview, the scope, the export).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import open_page
from streamlit.testing.v1 import AppTest

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import ManifestArtifact, PaperRecord
from research_gap_dashboard.dashboard.entry_log import (
  EntryLog,
  EntryRoute,
  LogEntry,
  PaperIdentity,
  build_entry_log_view,
  load_entry_log,
  mark_unavailable,
  reconcile_entry_log,
  record_entries,
)
from research_gap_dashboard.dashboard.paper_upload import place_paper_list

_DAY1 = datetime(2024, 3, 1, 9, tzinfo=timezone.utc)
_DAY2 = datetime(2024, 4, 2, 9, tzinfo=timezone.utc)
_DAY3 = datetime(2024, 5, 3, 9, tzinfo=timezone.utc)


def _paper(key: str, doi: str = "") -> PaperRecord:
  return PaperRecord(citation_key=key, doi=doi, title=f"Title of {key}")


def _manifest(root: Path, papers: list[PaperRecord]) -> ManifestArtifact:
  """Write a manifest with these Papers, dated _DAY1, and return its read model."""
  manifest = ManifestArtifact(corpus_root=root, papers=papers)
  path = root / "artifacts" / "corpus-manifest.json"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(manifest.model_dump_json(), encoding="utf-8")
  os.utime(path, (_DAY1.timestamp(), _DAY1.timestamp()))
  return manifest


# -- The entry_log seam -- #


def test_an_existing_corpus_gets_every_paper_logged_as_imported(tmp_path: Path):
  """A Corpus from before the log: every Paper is imported, dated by the manifest."""
  manifest = _manifest(tmp_path, [_paper("a"), _paper("b", "10.1/B")])

  view = reconcile_entry_log(tmp_path, manifest)

  assert [paper.route for paper in view.papers] == [EntryRoute.IMPORTED] * 2
  assert {paper.entered_at for paper in view.papers} == {_DAY1}
  assert view.summary == "2 imported"
  assert not view.grown_beyond_import
  logged = load_entry_log(tmp_path).entries
  assert {(entry.citation_key, entry.doi) for entry in logged} == {
    ("a", ""),
    ("b", "10.1/b"),
  }


def test_reconciling_again_keeps_the_first_date(tmp_path: Path):
  """A later ingest rewrites the manifest, but a logged Paper keeps its date."""
  manifest = _manifest(tmp_path, [_paper("a")])
  reconcile_entry_log(tmp_path, manifest)
  path = tmp_path / "artifacts" / "corpus-manifest.json"
  os.utime(path, (_DAY3.timestamp(), _DAY3.timestamp()))

  view = reconcile_entry_log(tmp_path, manifest)

  assert view.papers[0].entered_at == _DAY1


def test_papers_matched_by_doi_carry_their_logged_route(tmp_path: Path):
  """A suggestion logged by DOI is matched to the Paper ingest made of it."""
  record_entries(
    tmp_path,
    [PaperIdentity(doi="https://doi.org/10.9/Suggested")],
    EntryRoute.RETRIEVAL_SUGGESTION,
    at=_DAY2,
  )
  record_entries(
    tmp_path, [PaperIdentity(doi="10.9/hit")], EntryRoute.SATURATION_HIT, at=_DAY3
  )
  manifest = _manifest(
    tmp_path,
    [
      _paper("a"),
      _paper("b"),
      _paper("smith2023", "10.9/SUGGESTED"),
      _paper("jones2024", "10.9/hit"),
    ],
  )

  view = reconcile_entry_log(tmp_path, manifest)

  assert view.summary == (
    "2 imported \u00b7 1 from retrieval suggestions \u00b7 1 from saturation checks"
  )
  assert view.grown_beyond_import
  routes = {paper.citation_key: paper.route for paper in view.papers}
  assert routes["smith2023"] == EntryRoute.RETRIEVAL_SUGGESTION
  assert routes["jones2024"] == EntryRoute.SATURATION_HIT


def test_the_timeline_counts_papers_per_day_and_the_growing_corpus():
  """Each step is one day and route, oldest first, with the Corpus size after."""
  log = EntryLog(
    entries=[
      LogEntry(citation_key="a", route=EntryRoute.IMPORTED, entered_at=_DAY1),
      LogEntry(citation_key="b", route=EntryRoute.IMPORTED, entered_at=_DAY1),
      LogEntry(citation_key="c", route=EntryRoute.UPLOADED, entered_at=_DAY3),
      LogEntry(
        citation_key="d", route=EntryRoute.RETRIEVAL_SUGGESTION, entered_at=_DAY2
      ),
    ]
  )
  manifest = ManifestArtifact(
    corpus_root=Path("x"), papers=[_paper(key) for key in "abcd"]
  )

  timeline = build_entry_log_view(log, manifest).timeline

  assert [(s.day, s.route, s.count, s.corpus_size) for s in timeline] == [
    (_DAY1.date(), EntryRoute.IMPORTED, 2, 2),
    (_DAY2.date(), EntryRoute.RETRIEVAL_SUGGESTION, 1, 3),
    (_DAY3.date(), EntryRoute.UPLOADED, 1, 4),
  ]


def test_log_entries_that_never_became_papers_are_not_shown(tmp_path: Path):
  """An uploaded entry with no PDF is logged but not counted as a Paper."""
  record_entries(
    tmp_path, [PaperIdentity(citation_key="nopdf")], EntryRoute.UPLOADED, at=_DAY2
  )
  manifest = _manifest(tmp_path, [_paper("a")])

  view = reconcile_entry_log(tmp_path, manifest)

  assert [paper.citation_key for paper in view.papers] == ["a"]


def test_recording_a_paper_twice_keeps_its_first_route(tmp_path: Path):
  """Entering is a one-time event: a second record does not rewrite it."""
  record_entries(tmp_path, [PaperIdentity(citation_key="a")], EntryRoute.IMPORTED)
  added = record_entries(
    tmp_path, [PaperIdentity(citation_key="a")], EntryRoute.UPLOADED
  )

  assert added == 0
  assert [entry.route for entry in load_entry_log(tmp_path).entries] == [
    EntryRoute.IMPORTED
  ]


def test_unavailable_papers_are_disclosed_until_they_are_added(tmp_path: Path):
  """A paper marked unavailable is listed; once it enters, it no longer is."""
  mark_unavailable(
    tmp_path,
    doi="10.9/Paywalled",
    title="A paywalled study",
    route=EntryRoute.SATURATION_HIT,
    reason="no open-access copy",
    at=_DAY2,
  )
  manifest = _manifest(tmp_path, [_paper("a")])

  view = reconcile_entry_log(tmp_path, manifest)

  assert [(item.doi, item.title) for item in view.unavailable] == [
    ("10.9/paywalled", "A paywalled study")
  ]
  record_entries(
    tmp_path, [PaperIdentity(doi="10.9/paywalled")], EntryRoute.UPLOADED, at=_DAY3
  )
  assert load_entry_log(tmp_path).unavailable == []


def test_marking_the_same_paper_unavailable_again_replaces_it(tmp_path: Path):
  """The unavailable list never holds the same DOI twice."""
  for reason in ("first", "second"):
    mark_unavailable(
      tmp_path,
      doi="10.9/x",
      title="X",
      route=EntryRoute.RETRIEVAL_SUGGESTION,
      reason=reason,
    )

  assert [item.reason for item in load_entry_log(tmp_path).unavailable] == ["second"]


# -- Where the dashboard writes the log: the paper-list upload -- #


_BIB = """@article{alpha2020,
  title = {Alpha},
  doi = {10.1/alpha}
}

@article{beta2021,
  title = {Beta}
}
"""

_MORE_BIB = """@article{gamma2022,
  title = {Gamma},
  doi = {10.1/gamma}
}
"""


def test_a_first_paper_list_is_logged_as_imported(tmp_path: Path):
  """Before the first ingest, uploaded entries are the Corpus's initial import."""
  place_paper_list(tmp_path, "papers.bib", _BIB.encode())

  entries = load_entry_log(tmp_path).entries
  assert {(e.citation_key, e.doi, e.route) for e in entries} == {
    ("alpha2020", "10.1/alpha", EntryRoute.IMPORTED),
    ("beta2021", "", EntryRoute.IMPORTED),
  }


def test_a_paper_list_added_after_ingest_is_logged_as_uploaded(tmp_path: Path):
  """Once the Corpus has a manifest, newly listed entries were added by upload."""
  place_paper_list(tmp_path, "papers.bib", _BIB.encode())
  _manifest(tmp_path, [_paper("alpha2020", "10.1/alpha"), _paper("beta2021")])

  place_paper_list(tmp_path, "more.bib", (_MORE_BIB + _BIB).encode())

  routes = {e.citation_key: e.route for e in load_entry_log(tmp_path).entries}
  assert routes == {
    "alpha2020": EntryRoute.IMPORTED,
    "beta2021": EntryRoute.IMPORTED,
    "gamma2022": EntryRoute.UPLOADED,
  }


def test_an_uploaded_doi_list_is_logged_by_doi(tmp_path: Path):
  """A DOI list has no citation keys, so its entries are logged by DOI."""
  place_paper_list(tmp_path, "dois.txt", b"10.1/One\n# comment\n10.1/two\n")

  assert {e.doi for e in load_entry_log(tmp_path).entries} == {"10.1/one", "10.1/two"}


# -- Where the dashboard shows it -- #


@pytest.fixture(name="grown_corpus")
def grown_corpus_fixture(tmp_path: Path) -> Path:
  """Build a one-Corpus folder: two imported Papers, one suggestion, one unavailable."""
  root = tmp_path / "corpora" / "grown"
  for directory in ("papers", "paper-data", "judgments"):
    (root / directory).mkdir(parents=True)
  record_entries(
    root,
    [PaperIdentity(doi="10.9/suggested")],
    EntryRoute.RETRIEVAL_SUGGESTION,
    at=_DAY2,
  )
  mark_unavailable(
    root,
    doi="10.9/paywalled",
    title="A paywalled study",
    route=EntryRoute.SATURATION_HIT,
    at=_DAY3,
  )
  _manifest(root, [_paper("a"), _paper("b"), _paper("s2023", "10.9/suggested")])
  (root / "scope.json").write_text(
    json.dumps(
      {
        "question": "Q?",
        "field": "F",
        "searched": "PubMed",
        "recorded_at": _DAY1.isoformat(),
      }
    ),
    encoding="utf-8",
  )
  return tmp_path / "corpora"


def _app(corpora: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpora))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  return AppTest.from_file(str(app_path), default_timeout=30)


def _page_text(app: AppTest) -> str:
  parts = [element.value for element in app.markdown]
  parts += [element.value for element in app.info]
  parts += [element.value for element in app.warning]
  parts += [element.value for element in app.caption]
  return "\n".join(str(part) for part in parts)


def test_the_scope_discloses_how_the_corpus_was_built(
  grown_corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """Every page's scope carries the route summary and the unavailable papers."""
  app = _app(grown_corpus, monkeypatch)
  app.run()
  open_page(app, "Corpus Overview")

  assert not app.exception
  page = _page_text(app)
  assert "2 imported \u00b7 1 from retrieval suggestions" in page
  assert text.ENTRY_LOG_UNAVAILABLE_SCOPE.format(count=1) in page


def test_the_overview_shows_the_timeline_and_unavailable_papers(
  grown_corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """The Overview lists the growth steps and names the unavailable paper."""
  app = _app(grown_corpus, monkeypatch)
  app.run()
  open_page(app, "Corpus Overview")

  assert not app.exception
  assert text.ENTRY_LOG_HEADING in [header.value for header in app.subheader]
  tables = [str(table.value) for table in app.table]
  assert any(
    "2024-04-02" in table and "Retrieval suggestion" in table for table in tables
  )
  assert any("A paywalled study" in table for table in tables)
