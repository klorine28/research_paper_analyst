"""
Creating a Corpus from the dashboard and recording its scope statement (#74).

The seam is `dashboard.corpus_scope` (create, save, load) plus the shell, driven
through `AppTest` the way the researcher meets it: a "New analysis" form, the
scope statement on every page, and a prompt for Corpora created before scopes
existed.
"""

from conftest import open_page
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import discover_corpora
from research_gap_dashboard.dashboard.corpus_scope import (
  CorpusCreationError,
  create_corpus,
  load_scope,
  save_scope,
)


def _manifest_corpus(root: Path) -> None:
  """Write a one-Paper Corpus with a manifest and no scope statement."""
  (root / "artifacts").mkdir(parents=True)
  (root / "papers").mkdir()
  manifest = {
    "corpus_root": str(root),
    "papers": [{"citation_key": "paper-a", "title": "A study"}],
    "unmatched_entries": [],
    "orphan_pdfs": [],
  }
  (root / "artifacts" / "corpus-manifest.json").write_text(
    json.dumps(manifest), encoding="utf-8"
  )


# -- The corpus_scope seam -- #


def test_create_corpus_lays_out_the_directory_and_records_its_scope(tmp_path: Path):
  """A new Corpus gets the standard directories and its scope on disk."""
  choice = create_corpus(
    tmp_path,
    name="takotsubo-review",
    question="What is known about Takotsubo in men?",
    field="Cardiology",
    searched="PubMed, 2015-2024, 'takotsubo AND male'",
  )

  assert choice.name == "takotsubo-review"
  assert choice.root == tmp_path / "takotsubo-review"
  for directory in ("papers", "paper-data", "artifacts", "judgments"):
    assert (choice.root / directory).is_dir()
  scope = load_scope(choice.root)
  assert scope is not None
  assert scope.question == "What is known about Takotsubo in men?"
  assert scope.field == "Cardiology"
  assert scope.searched == "PubMed, 2015-2024, 'takotsubo AND male'"


def test_a_new_corpus_is_offered_before_it_has_any_papers(tmp_path: Path):
  """A freshly created Corpus, with no manifest yet, is still discoverable."""
  create_corpus(tmp_path, name="fresh", question="Q", field="F", searched="S")

  assert [choice.name for choice in discover_corpora(tmp_path)] == ["fresh"]


@pytest.mark.parametrize("name", ["", "   ", "../escape", "a/b", ".hidden", "a\\b"])
def test_an_unsafe_or_blank_name_is_refused(tmp_path: Path, name: str):
  """The name becomes a directory, so it must be one plain path segment."""
  with pytest.raises(CorpusCreationError):
    create_corpus(tmp_path, name=name, question="Q", field="F", searched="S")
  assert not list(tmp_path.iterdir())


def test_an_existing_directory_is_never_overwritten(tmp_path: Path):
  """Creating a Corpus whose name is taken refuses rather than merging into it."""
  (tmp_path / "taken").mkdir()
  (tmp_path / "taken" / "keep.txt").write_text("mine", encoding="utf-8")

  with pytest.raises(CorpusCreationError):
    create_corpus(tmp_path, name="taken", question="Q", field="F", searched="S")
  assert load_scope(tmp_path / "taken") is None


@pytest.mark.parametrize("blank", ["question", "field", "searched"])
def test_every_part_of_the_scope_is_required(tmp_path: Path, blank: str):
  """A scope with a blank part would state nothing, so it is refused."""
  parts = {"question": "Q", "field": "F", "searched": "S", blank: "  "}
  with pytest.raises(CorpusCreationError):
    create_corpus(tmp_path, name="corpus", **parts)
  assert not (tmp_path / "corpus").exists()


def test_a_corpus_without_a_scope_has_none(tmp_path: Path):
  """Corpora made before scopes existed read as having no scope."""
  _manifest_corpus(tmp_path / "old")

  assert load_scope(tmp_path / "old") is None


def test_save_scope_adds_a_scope_to_an_existing_corpus(tmp_path: Path):
  """An existing Corpus can be given a scope, which is then read back trimmed."""
  _manifest_corpus(tmp_path / "old")

  save_scope(tmp_path / "old", question=" Q ", field="F", searched="S")

  scope = load_scope(tmp_path / "old")
  assert scope is not None
  assert (scope.question, scope.field, scope.searched) == ("Q", "F", "S")


# -- The shell -- #


def _run_app(corpora_dir: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
  """Run the shell against a corpora folder."""
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpora_dir))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30)
  return app.run()


def _fill_new_analysis(app: AppTest, name: str) -> AppTest:
  """Fill in and submit the New analysis form."""
  values = {
    text.NEW_CORPUS_NAME_LABEL: name,
    text.SCOPE_QUESTION_LABEL: "Does X cause Y?",
    text.SCOPE_FIELD_LABEL: "Epidemiology",
    text.SCOPE_SEARCHED_LABEL: "Scopus, 2010-2024",
  }
  for widget in [*app.text_input, *app.text_area]:
    if widget.key and widget.key.startswith("new_corpus") and widget.label in values:
      widget.set_value(values[widget.label])
  submit = next(b for b in app.button if b.label == text.NEW_CORPUS_SUBMIT)
  return submit.click().run()


def _page_text(app: AppTest) -> str:
  """Join every info, warning and caption block into one searchable string."""
  blocks = [*app.info, *app.warning, *app.caption, *app.markdown, *app.success]
  return " ".join(str(block.value) for block in blocks)


def test_a_corpus_is_created_from_an_empty_folder_without_a_terminal(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """With no Corpora at all, the New analysis form creates one and opens it."""
  app = _run_app(tmp_path, monkeypatch)
  assert not app.exception

  app = _fill_new_analysis(app, "my-review")

  assert not app.exception
  assert (tmp_path / "my-review" / "papers").is_dir()
  scope = load_scope(tmp_path / "my-review")
  assert scope is not None
  assert scope.question == "Does X cause Y?"
  shown = _page_text(app)
  assert "Does X cause Y?" in shown
  assert "Epidemiology" in shown
  assert "Scopus, 2010-2024" in shown
  assert text.CORPUS_HAS_NO_PAPERS.split(".", maxsplit=1)[0] in shown


def test_a_refused_name_is_explained_in_the_app(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """A name that is already taken shows an error instead of crashing."""
  create_corpus(tmp_path, name="taken", question="Q", field="F", searched="S")
  app = _run_app(tmp_path, monkeypatch)

  app = _fill_new_analysis(app, "taken")

  assert not app.exception
  assert app.error
  kept = load_scope(tmp_path / "taken")
  assert kept is not None
  assert kept.question == "Q"


def test_the_scope_statement_is_shown_on_every_page(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """Whichever page is open, the Corpus states what it was built from."""
  _manifest_corpus(tmp_path / "corpus")
  save_scope(
    tmp_path / "corpus", question="Is Z rare?", field="Genetics", searched="OpenAlex"
  )
  app = _run_app(tmp_path, monkeypatch)

  for page in (text.PAGE_OVERVIEW, text.PAGE_GAP_CARDS, text.PAGE_ATTENTION):
    open_page(app, page)
    assert not app.exception
    shown = _page_text(app)
    assert "Is Z rare?" in shown, page
    assert "Genetics" in shown, page
    assert "OpenAlex" in shown, page


def test_a_corpus_without_a_scope_prompts_for_one(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """An older Corpus asks for its scope, and the answer is saved and shown."""
  _manifest_corpus(tmp_path / "corpus")
  app = _run_app(tmp_path, monkeypatch)
  assert text.SCOPE_MISSING_PROMPT in _page_text(app)

  values = {
    text.SCOPE_QUESTION_LABEL: "Is Z rare?",
    text.SCOPE_FIELD_LABEL: "Genetics",
    text.SCOPE_SEARCHED_LABEL: "OpenAlex",
  }
  for widget in [*app.text_input, *app.text_area]:
    if widget.key and widget.key.startswith("add_scope") and widget.label in values:
      widget.set_value(values[widget.label])
  save = next(b for b in app.button if b.label == text.SCOPE_SAVE_BUTTON)
  app = save.click().run()

  assert not app.exception
  scope = load_scope(tmp_path / "corpus")
  assert scope is not None
  assert scope.field == "Genetics"
  shown = _page_text(app)
  assert text.SCOPE_MISSING_PROMPT not in shown
  assert "Is Z rare?" in shown
