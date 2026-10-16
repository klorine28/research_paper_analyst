"""
The Grow path in the dashboard: add a suggested Paper from either route (#91).

Read-model tests check the double signal (a work that is both a Retrieval Gap
candidate and a saturation hit is marked in both places) and that unavailable
papers are kept apart. Seam tests patch the runner's subprocess, so the real
CLI never runs, and assert the commands the dashboard asks for: the add, an
upload handed over as a file, "mark unavailable" with its reason, and the
incremental re-run that follows an add.
"""

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import open_page
from streamlit.testing.v1 import AppTest
from test_retrieval import _PAPERS, _WORKS, _openalex, _write_manifest
from test_saturate import FILLED_GAP, FIXTURE, _check

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import pipeline_runner, text
from research_gap_dashboard.dashboard.trust_gate import acknowledge_denominator
from research_gap_dashboard.dashboard.artifacts import (
  RetrievalGapsArtifact,
  load_candidate_gaps,
  load_retrieval_gaps,
  load_saturation_checks,
)
from research_gap_dashboard.dashboard.gaps import build_gap_cards
from research_gap_dashboard.dashboard.judgments import load_judgments
from research_gap_dashboard.dashboard.retrieval import build_retrieval_gaps
from research_gap_dashboard.dashboard.suggestions import (
  RERUN_STAGES,
  Suggestion,
  add_suggestion,
  load_suggested_papers,
  mark_suggestion_unavailable,
)
from research_gap_dashboard.grow import WorkRef, mark_unavailable
from research_gap_dashboard.retrieval import detect_retrieval_gaps

_PDF = b"%PDF-1.4 uploaded"


class _FakeCli:
  """Stands in for `subprocess.run`: records commands, answers `add-suggested`."""

  def __init__(self, add_status: str = "added") -> None:
    self.calls: list[list[str]] = []
    self.uploads: list[bytes] = []
    self.add_status = add_status

  def __call__(self, args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
    self.calls.append(list(args))
    if args[3] != "add-suggested":
      return subprocess.CompletedProcess(args, 0, "", "")
    if "--upload" in args:
      self.uploads.append(Path(args[args.index("--upload") + 1]).read_bytes())
    code = {"added": 0, "pending": 2}.get(self.add_status, 1)
    printed = json.dumps({"status": self.add_status, "message": "done"})
    return subprocess.CompletedProcess(args, code, printed, "")

  def commands(self) -> list[list[str]]:
    """Each command's name and options, without the runner prefix and Corpus."""
    return [[call[3], *call[5:]] for call in self.calls]


@pytest.fixture(name="fake_cli")
def fake_cli_fixture(monkeypatch: pytest.MonkeyPatch) -> _FakeCli:
  """Patch the runner's subprocess; allow the re-run's LLM stages to launch."""
  fake = _FakeCli()
  monkeypatch.setattr(pipeline_runner.subprocess, "run", fake)
  monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
  monkeypatch.setenv("LLM_COST_CONFIRM_ABOVE", "1000000")
  return fake


@pytest.fixture(name="retrieval_corpus")
def retrieval_corpus_fixture(tmp_path: Path) -> Path:
  """Return a Corpus, alone in its folder, with a RetrievalGaps artifact."""
  root = tmp_path / "corpora" / "corpus"
  root.mkdir(parents=True)
  _write_manifest(root, _PAPERS)
  detect_retrieval_gaps(root, _openalex(_WORKS), min_overlap=1)
  return root


@pytest.fixture(name="saturation_corpus")
def saturation_corpus_fixture(tmp_path: Path) -> Path:
  """Return the saturation fixture Corpus with one checked gap, alone in its folder."""
  root = Path(shutil.copytree(FIXTURE, tmp_path / "corpora" / "saturation-corpus"))
  _check(root, FILLED_GAP)
  return root


def _retrieval_with(hit_doi: str, root: Path) -> RetrievalGapsArtifact:
  """Return a RetrievalGaps artifact whose one candidate is the given work."""
  return RetrievalGapsArtifact.model_validate(
    {
      "corpus_root": str(root),
      "candidates": [
        {
          "openalex_id": "https://openalex.org/W9",
          "doi": hit_doi.upper(),
          "title": "Also a hit",
          "citation_overlap": 3,
        }
      ],
    }
  )


def test_a_work_found_by_both_routes_is_marked_in_both_places(saturation_corpus: Path):
  """A hit that is a Retrieval Gap says so, and the candidate names the gap."""
  checks = load_saturation_checks(saturation_corpus)
  hit = next(hit for hit in checks.checks[-1].hits if hit.doi)
  retrieval = _retrieval_with(hit.doi, saturation_corpus)

  cards = build_gap_cards(
    load_candidate_gaps(saturation_corpus),
    load_judgments(saturation_corpus),
    saturation=checks,
    retrieval=retrieval,
  )
  candidates = build_retrieval_gaps(retrieval, checks).candidates

  card = next(card for card in cards.cards if card.gap_id == FILLED_GAP)
  marked = [view for view in card.saturation.hits if view.retrieval_overlap]
  assert [view.title for view in marked] == [hit.title]
  assert marked[0].retrieval_overlap == 3
  assert candidates[0].saturation_gap_ids == [FILLED_GAP]


def test_unavailable_papers_are_kept_apart_with_their_reason(retrieval_corpus: Path):
  """An unavailable candidate is no longer offered, but listed with its reason."""
  mark_unavailable(
    retrieval_corpus,
    WorkRef(doi="10.1/aaa"),
    "retrieval",
    "Not held by the library",
    now=datetime(2026, 5, 4, tzinfo=UTC),
  )

  data = build_retrieval_gaps(
    load_retrieval_gaps(retrieval_corpus),
    suggested=load_suggested_papers(retrieval_corpus),
  )

  assert "10.1/aaa" not in [candidate.doi for candidate in data.candidates]
  assert [candidate.doi for candidate in data.unavailable] == ["10.1/aaa"]
  assert data.unavailable[0].state.reason == "Not held by the library"
  assert data.unavailable[0].state.updated_on == "2026-05-04"
  assert data.candidate_count == len(data.candidates) + 1
  assert len(load_suggested_papers(retrieval_corpus).unavailable) == 1


def test_an_unavailable_hit_is_flagged_on_its_card(saturation_corpus: Path):
  """A hit marked unavailable carries that state, so the card sets it apart."""
  checks = load_saturation_checks(saturation_corpus)
  hit = next(hit for hit in checks.checks[-1].hits if hit.doi)
  mark_unavailable(saturation_corpus, WorkRef(doi=hit.doi), "saturation", "Paywalled")

  cards = build_gap_cards(
    load_candidate_gaps(saturation_corpus),
    load_judgments(saturation_corpus),
    saturation=checks,
    suggested=load_suggested_papers(saturation_corpus),
  )

  card = next(card for card in cards.cards if card.gap_id == FILLED_GAP)
  flagged = [view.title for view in card.saturation.hits if view.state.unavailable]
  assert flagged == [hit.title]


def test_add_runs_the_pipeline_command_and_reads_its_outcome(
  tmp_path: Path, fake_cli: _FakeCli
):
  """The dashboard asks the pipeline to add the work; it calls no API itself."""
  suggestion = Suggestion(route="saturation", doi="10.1/x", pmid="7", title="T")

  result = add_suggestion(tmp_path, suggestion)

  assert result.status == "added"
  assert fake_cli.commands() == [
    [
      "add-suggested",
      "--route",
      "saturation",
      "--doi",
      "10.1/x",
      "--pmid",
      "7",
      "--title",
      "T",
    ]
  ]


def test_an_upload_is_handed_to_the_pipeline_as_a_file(
  tmp_path: Path, fake_cli: _FakeCli
):
  """The uploaded PDF reaches the pipeline intact; nothing is put in papers/."""
  add_suggestion(tmp_path, Suggestion(route="retrieval", doi="10.1/x"), upload=_PDF)

  assert fake_cli.uploads == [_PDF]
  assert not (tmp_path / "papers").exists()


def test_mark_unavailable_passes_the_reason(tmp_path: Path, fake_cli: _FakeCli):
  """Marking unavailable runs the pipeline command with the reason."""
  mark_suggestion_unavailable(
    tmp_path, Suggestion(route="retrieval", openalex_id="W9"), "No access"
  )

  assert fake_cli.commands() == [
    [
      "mark-unavailable",
      "--route",
      "retrieval",
      "--openalex-id",
      "W9",
      "--reason",
      "No access",
    ]
  ]


def _open_retrieval(corpus: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpus.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()
  return open_page(app, text.PAGE_RETRIEVAL)


def test_adding_a_candidate_triggers_the_incremental_rerun(
  retrieval_corpus: Path, fake_cli: _FakeCli, monkeypatch: pytest.MonkeyPatch
):
  """Add to Corpus runs `add-suggested`, then re-runs the stages via the runner."""
  app = _open_retrieval(retrieval_corpus, monkeypatch)
  assert not app.exception

  button = next(b for b in app.button if b.label == text.SUGGEST_ADD_BUTTON)
  button.click().run()

  assert not app.exception
  commands = fake_cli.commands()
  assert commands[0][:3] == ["add-suggested", "--route", "retrieval"]
  assert [command[0] for command in commands[1:]] == list(RERUN_STAGES)


def test_a_pending_add_does_not_rerun_and_offers_upload(
  retrieval_corpus: Path, fake_cli: _FakeCli, monkeypatch: pytest.MonkeyPatch
):
  """No PDF: nothing re-runs, and the outcome says the paper is not added yet."""
  fake_cli.add_status = "pending"
  app = _open_retrieval(retrieval_corpus, monkeypatch)

  next(b for b in app.button if b.label == text.SUGGEST_ADD_BUTTON).click().run()

  assert not app.exception
  assert [command[0] for command in fake_cli.commands()] == ["add-suggested"]
  assert text.SUGGEST_PENDING.format(message="done") in [w.value for w in app.warning]


def test_a_pending_paper_can_be_marked_unavailable(
  retrieval_corpus: Path, fake_cli: _FakeCli, monkeypatch: pytest.MonkeyPatch
):
  """A pending paper offers upload and "mark unavailable" with a reason."""
  (retrieval_corpus / "suggested_papers.json").write_text(
    json.dumps(
      {
        "papers": [
          {
            "doi": "10.1/aaa",
            "routes": ["retrieval"],
            "status": "pending",
            "reason": "no open-access copy is known",
            "updated_at": "2026-05-04T00:00:00Z",
          }
        ]
      }
    ),
    encoding="utf-8",
  )
  app = _open_retrieval(retrieval_corpus, monkeypatch)
  assert not app.exception
  captions = " ".join(caption.value for caption in app.caption)
  assert "no open-access copy is known" in captions

  app.text_input(key="reason-retrieval-doi-10-1-aaa").input("Library has no access")
  submit = next(b for b in app.button if b.label == text.SUGGEST_UNAVAILABLE_SUBMIT)
  submit.click().run()

  assert not app.exception
  assert fake_cli.commands()[-1][0] == "mark-unavailable"
  assert fake_cli.commands()[-1][-2:] == ["--reason", "Library has no access"]


def test_a_hit_is_added_from_its_gap_card(
  saturation_corpus: Path, fake_cli: _FakeCli, monkeypatch: pytest.MonkeyPatch
):
  """The card's hit picker offers the same Grow path, as a saturation route."""
  # The fixture's Papers have no Extractions, so the Trust gate (#77) would lock
  # the Judge step; acknowledge the denominator as a researcher would.
  acknowledge_denominator(saturation_corpus)
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(saturation_corpus.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()
  open_page(app, text.PAGE_GAP_CARDS)
  app.multiselect[0].set_value(["high", "medium", "low"]).run()
  assert not app.exception

  button = next(b for b in app.button if b.label == text.SUGGEST_ADD_BUTTON)
  button.click().run()

  assert not app.exception
  first = fake_cli.commands()[0]
  assert first[:3] == ["add-suggested", "--route", "saturation"]
  assert [command[0] for command in fake_cli.commands()[1:]] == list(RERUN_STAGES)
