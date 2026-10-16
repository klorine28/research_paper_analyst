"""
The Grow path writes the Corpus entry log (#169, ADR 0010 §4).

A suggested Paper added through `grow` is logged with its route and date, a
paper marked unavailable is recorded in the entry log only, and a Corpus whose
older `suggested_papers.json` marked papers unavailable keeps them: they are
carried into the log, which the dashboard and the Overview read once.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import open_page
from streamlit.testing.v1 import AppTest
from test_grow import _make_corpus

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import ManifestArtifact, PaperRecord
from research_gap_dashboard.dashboard.entry_log import (
  EntryRoute,
  load_entry_log,
  reconcile_entry_log,
)
from research_gap_dashboard.dashboard.suggestions import (
  load_suggested_papers,
  suggestion_state,
  work_ids,
)
from research_gap_dashboard.grow import (
  Route,
  WorkRef,
  add_suggested_paper,
  mark_unavailable,
  read_suggested_papers,
)
from research_gap_dashboard.sources import OpenAlexAdapter

_PDF = b"%PDF-1.7 a suggested paper"
_NOW = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
_EARLIER = datetime(2025, 1, 2, 8, 0, tzinfo=UTC)


def _offline_openalex() -> OpenAlexAdapter:
  return OpenAlexAdapter(fetch=lambda _url: None)


def _no_download(_url: str) -> bytes | None:
  return None


def _manifest(root: Path, keys_and_dois: list[tuple[str, str]]) -> ManifestArtifact:
  manifest = ManifestArtifact(
    corpus_root=root,
    papers=[
      PaperRecord(citation_key=key, doi=doi, title=f"Title of {key}")
      for key, doi in keys_and_dois
    ],
  )
  path = root / "artifacts" / "corpus-manifest.json"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(manifest.model_dump_json(), encoding="utf-8")
  return manifest


@pytest.fixture(name="corpus")
def corpus_fixture(tmp_path: Path) -> Path:
  """Return a Corpus of ten paired Papers listed in BibTeX."""
  return _make_corpus(tmp_path / "corpora" / "grown")


@pytest.mark.parametrize(
  ("route", "expected"),
  [
    ("retrieval", EntryRoute.RETRIEVAL_SUGGESTION),
    ("saturation", EntryRoute.SATURATION_HIT),
  ],
)
def test_an_added_suggestion_enters_the_log_with_its_route_and_date(
  corpus: Path, route: Route, expected: EntryRoute
):
  """The added Paper shows on the Overview by its route, not as imported."""
  outcome = add_suggested_paper(
    corpus,
    WorkRef(doi="10.5555/new"),
    route,
    openalex=_offline_openalex(),
    download=_no_download,
    upload=_PDF,
    title="A new study",
    now=_NOW,
  )
  assert outcome.paper is not None
  key = outcome.paper.citation_key
  manifest = _manifest(
    corpus, [(f"paper{i:02d}", f"10.1/p{i}") for i in range(10)] + [(key, "")]
  )

  view = reconcile_entry_log(corpus, manifest)

  added = next(paper for paper in view.papers if paper.citation_key == key)
  assert (added.route, added.entered_at) == (expected, _NOW)
  assert view.grown_beyond_import


def test_marking_unavailable_writes_the_entry_log_only(corpus: Path):
  """The log is the one record; the suggested-Papers file holds no such status."""
  mark_unavailable(
    corpus,
    WorkRef(pmid="12345"),
    "retrieval",
    "Not held by the library",
    title="A closed study",
    now=_NOW,
  )

  log = load_entry_log(corpus)
  assert [(item.pmid, item.title, item.route) for item in log.unavailable] == [
    ("12345", "A closed study", EntryRoute.RETRIEVAL_SUGGESTION)
  ]
  raw = json.loads((corpus / "suggested_papers.json").read_text(encoding="utf-8"))
  assert all(paper["status"] != "unavailable" for paper in raw["papers"])
  state = suggestion_state(load_suggested_papers(corpus), work_ids("", "12345", ""))
  assert (state.unavailable, state.reason) == (True, "Not held by the library")


def test_marking_a_pending_paper_unavailable_wins_over_its_pending_record(
  corpus: Path,
):
  """A pending fetch, then "unavailable": the dashboard shows it unavailable."""
  add_suggested_paper(
    corpus,
    WorkRef(doi="10.5555/closed"),
    "saturation",
    openalex=_offline_openalex(),
    download=_no_download,
    now=_EARLIER,
  )
  assert read_suggested_papers(corpus).papers[0].status == "pending"

  mark_unavailable(corpus, WorkRef(doi="10.5555/closed"), "saturation", "Paywalled")

  assert read_suggested_papers(corpus).papers == []
  state = suggestion_state(
    load_suggested_papers(corpus), work_ids("10.5555/closed", "", "")
  )
  assert state.unavailable


def _legacy_file(root: Path) -> None:
  """Write a `suggested_papers.json` from before the log owned "unavailable"."""
  record = {
    "doi": "10.5555/old",
    "title": "An old closed study",
    "routes": ["saturation"],
    "status": "unavailable",
    "reason": "Library has no access",
    "updated_at": _EARLIER.isoformat(),
    "history": [
      {"at": _EARLIER.isoformat(), "status": "unavailable", "route": "saturation"}
    ],
  }
  pending = {
    "doi": "10.5555/waiting",
    "routes": ["retrieval"],
    "status": "pending",
    "updated_at": _EARLIER.isoformat(),
  }
  (root / "suggested_papers.json").write_text(
    json.dumps({"papers": [record, pending]}), encoding="utf-8"
  )


def test_legacy_unavailable_papers_are_carried_into_the_log(corpus: Path):
  """An older file still loads, and its unavailable papers are not lost."""
  _legacy_file(corpus)
  manifest = _manifest(corpus, [("paper00", "10.1/p0")])

  view = reconcile_entry_log(corpus, manifest)
  reconcile_entry_log(corpus, manifest)

  assert [
    (item.doi, item.route, item.marked_at, item.reason) for item in view.unavailable
  ] == [("10.5555/old", EntryRoute.SATURATION_HIT, _EARLIER, "Library has no access")]
  assert len(load_entry_log(corpus).unavailable) == 1
  suggested = load_suggested_papers(corpus)
  assert [paper.status for paper in suggested.papers] == ["pending", "unavailable"]


def test_the_grow_path_drops_legacy_unavailable_records_on_its_next_write(
  corpus: Path,
):
  """Reading the old file through `grow` moves "unavailable" into the log."""
  _legacy_file(corpus)

  assert [paper.status for paper in read_suggested_papers(corpus).papers] == ["pending"]
  assert [item.doi for item in load_entry_log(corpus).unavailable] == ["10.5555/old"]

  add_suggested_paper(
    corpus,
    WorkRef(doi="10.5555/old"),
    "saturation",
    openalex=_offline_openalex(),
    download=_no_download,
    upload=_PDF,
    now=_NOW,
  )

  raw = json.loads((corpus / "suggested_papers.json").read_text(encoding="utf-8"))
  assert sorted(paper["status"] for paper in raw["papers"]) == ["added", "pending"]
  assert load_entry_log(corpus).unavailable == []


def test_the_overview_discloses_an_unavailable_paper_once(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """Only the entry log's count is shown: no second count from the old record."""
  for directory in ("paper-data", "judgments"):
    (corpus / directory).mkdir()
  _legacy_file(corpus)
  _manifest(corpus, [(f"paper{i:02d}", f"10.1/p{i}") for i in range(10)])
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpus.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30)
  app.run()
  open_page(app, "Corpus Overview")

  assert not app.exception
  captions = [str(caption.value) for caption in app.caption]
  label = text.ENTRY_LOG_UNAVAILABLE_LABEL.format(count=1)
  assert sum(label in caption for caption in captions) == 1
  assert not any("could not be included" in caption for caption in captions)
  tables = [str(table.value) for table in app.table]
  assert any("An old closed study" in table for table in tables)
