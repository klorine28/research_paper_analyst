"""
Behavior of the Paper Comparison view: the artifacts in, comparison data out.

The view reads the Extractions the extract stage writes (field-by-field facts)
and the NormalizedFacts the aggregate stage writes (shared categories), so a
schema drift between writer and the dashboard's readers would surface here. The
tests build real artifacts on disk and read them back through the dashboard's own
readers. Agreement highlighting and blind spots are grounded in the shared
categories, never guessed from free text, and every count states the Corpus it
was computed against.
"""

from pathlib import Path

import pytest

import research_gap_dashboard.dashboard as dashboard_pkg

from research_gap_dashboard.aggregate import (
  AggregateReport,
  CategoryAssignment,
  NormalizedFacts,
)
from research_gap_dashboard.dashboard.artifacts import (
  has_extractions,
  load_extractions,
  ArtifactNotFoundError,
  load_manifest,
  load_normalized_facts,
)
from research_gap_dashboard.dashboard.comparison import (
  MAX_SELECTION,
  SelectionError,
  build_comparison,
  chunk_selection,
  selection_error,
)
from research_gap_dashboard.extract import (
  Evidence,
  ExtractedFact,
  Extraction,
  ExtractionFields,
  ExtractReport,
)
from research_gap_dashboard.ingest import CorpusManifest, Paper
from research_gap_dashboard.taxonomy import Axis

# (axis, category id, label) placements per Paper, chosen so a selection has a
# clear agreement (both takotsubo/retrospective), a difference (only p01 is
# aged), and blind spots (heart-failure, rct, ehr no small selection covers).
_LABELS = {
  "takotsubo": "Takotsubo Cardiomyopathy",
  "heart-failure": "Heart Failure",
  "retrospective": "Retrospective Studies",
  "rct": "Randomized Controlled Trial",
  "aged": "Aged",
  "ehr": "Electronic Health Records",
}

Placement = tuple[Axis, str]
_PLACEMENTS: dict[str, list[Placement]] = {
  "p01": [("topic", "takotsubo"), ("method", "retrospective"), ("population", "aged")],
  "p02": [("topic", "takotsubo"), ("method", "retrospective")],
  "p03": [("topic", "heart-failure"), ("method", "rct"), ("dataset", "ehr")],
  "p04": [("topic", "heart-failure"), ("method", "retrospective")],
  "p05": [("topic", "takotsubo"), ("method", "rct")],
  "p06": [("topic", "heart-failure"), ("method", "retrospective")],
}

# One statement per field per Paper, so field-by-field output is easy to assert.
_STATEMENTS: dict[str, dict[str, str]] = {
  key: {
    "research_question": f"{key} asks about outcomes.",
    "methods": f"{key} used a study design.",
    "populations": f"{key} studied patients.",
    "datasets": f"{key} used records.",
    "key_findings": f"{key} found a result.",
    "limitations": f"{key} was limited.",
  }
  for key in _PLACEMENTS
}


def _facts(key: str, placements: list[Placement]) -> NormalizedFacts:
  """Build one Paper's NormalizedFacts, each placement backed by Evidence."""
  return NormalizedFacts(
    citation_key=key,
    assignments=[
      CategoryAssignment(
        axis=axis,
        original_term=f"{category} term",
        category_id=category,
        category_label=_LABELS[category],
        fact_ref=f"{axis}s[{index}]",
        evidence=Evidence(passage=f"{key} on {category}", section="methods"),
      )
      for index, (axis, category) in enumerate(placements)
    ],
    unmapped=[],
  )


def _fact(statement: str) -> ExtractedFact:
  """Build one ExtractedFact with Evidence for a statement."""
  return ExtractedFact(
    statement=statement,
    evidence=Evidence(passage=statement, section="methods"),
  )


def _extraction(key: str) -> Extraction:
  """Build one Paper's Extraction with one fact in each compared field."""
  said = _STATEMENTS[key]
  return Extraction(
    citation_key=key,
    fields=ExtractionFields(
      research_question=[_fact(said["research_question"])],
      methods=[_fact(said["methods"])],
      populations=[_fact(said["populations"])],
      datasets=[_fact(said["datasets"])],
      key_findings=[_fact(said["key_findings"])],
      limitations=[_fact(said["limitations"])],
    ),
  )


def _write_corpus(root: Path, keys: list[str]) -> Path:
  """Write the manifest, Extractions, and NormalizedFacts for the given Papers."""
  for name in ("papers", "paper-data", "artifacts", "judgments"):
    (root / name).mkdir(parents=True, exist_ok=True)
  manifest = CorpusManifest(
    corpus_root=root,
    papers=[
      Paper(
        citation_key=key,
        doi=f"10.1/{key}",
        pdf_path=root / f"{key}.pdf",
        title=f"Study {key}",
        year=2020,
      )
      for key in keys
    ],
    unmatched_entries=[],
    orphan_pdfs=[],
  )
  normalized = AggregateReport(
    corpus_root=root,
    prompt_version="aggregate-v2",
    tier="default",
    taxonomies=[],
    normalized=[_facts(key, _PLACEMENTS[key]) for key in keys],
    failures=[],
  )
  extracted = ExtractReport(
    corpus_root=root,
    prompt_version="extract-v1",
    tier="default",
    extractions=[_extraction(key) for key in keys],
    failures=[],
  )
  artifacts = root / "artifacts"
  (artifacts / "corpus-manifest.json").write_text(
    manifest.model_dump_json(indent=2), encoding="utf-8"
  )
  (artifacts / "normalized_facts.json").write_text(
    normalized.model_dump_json(indent=2), encoding="utf-8"
  )
  (artifacts / "extractions.json").write_text(
    extracted.model_dump_json(indent=2), encoding="utf-8"
  )
  return root


@pytest.fixture(name="corpus")
def corpus_fixture(tmp_path: Path) -> Path:
  """Write a six-Paper Corpus with extractions and normalized facts on disk."""
  return _write_corpus(tmp_path / "corpus", list(_PLACEMENTS))


def _build(corpus: Path, selection: list[str]):
  """Build a comparison for a selection, reading through the dashboard readers."""
  return build_comparison(
    selection,
    load_extractions(corpus),
    load_normalized_facts(corpus),
    load_manifest(corpus),
  )


# -- Chunking --------------------------------------------------------------


def test_chunk_selection_keeps_a_small_selection_in_one_set() -> None:
  """A selection that fits the direct limit is returned as a single set."""
  assert chunk_selection(["a", "b", "c"]) == [["a", "b", "c"]]
  assert chunk_selection(["a", "b", "c", "d", "e"]) == [["a", "b", "c", "d", "e"]]


def test_chunk_selection_splits_evenly_and_never_exceeds_five() -> None:
  """A larger selection splits into balanced sets of at most five, order kept."""
  assert chunk_selection(list("abcdef")) == [["a", "b", "c"], ["d", "e", "f"]]
  eleven = chunk_selection(list("abcdefghijk"))
  assert [len(chunk) for chunk in eleven] == [4, 4, 3]
  assert [item for chunk in eleven for item in chunk] == list("abcdefghijk")
  assert all(len(chunk) <= 5 for chunk in chunk_selection(list("abcdefghijklmno")))


# -- Selection validation --------------------------------------------------


def test_selection_error_needs_at_least_two_distinct_papers(corpus: Path) -> None:
  """Fewer than two distinct Papers cannot be compared."""
  manifest = load_manifest(corpus)
  assert selection_error(["p01"], manifest) is not None
  assert selection_error(["p01", "p01"], manifest) is not None


def test_selection_error_rejects_too_many_or_unknown_papers(corpus: Path) -> None:
  """More than fifteen Papers, or a key not in the Corpus, is rejected."""
  manifest = load_manifest(corpus)
  too_many = [f"x{index}" for index in range(MAX_SELECTION + 1)]
  assert selection_error(too_many, manifest) is not None
  unknown = selection_error(["p01", "ghost"], manifest)
  assert unknown is not None and "ghost" in unknown


def test_selection_error_accepts_a_valid_selection(corpus: Path) -> None:
  """Two to fifteen distinct in-Corpus Papers compare cleanly."""
  assert selection_error(["p01", "p02"], load_manifest(corpus)) is None


def test_build_comparison_rejects_an_unusable_selection(corpus: Path) -> None:
  """build_comparison raises rather than compare an unusable selection."""
  with pytest.raises(SelectionError):
    _build(corpus, ["p01"])


def test_extractions_reader_reflects_presence_on_disk(corpus: Path) -> None:
  """has_extractions is true once written and a clear error when it is missing."""
  assert has_extractions(corpus)
  with pytest.raises(ArtifactNotFoundError, match="extract"):
    load_extractions(corpus.parent)


# -- Direct comparison (2-5 Papers) ---------------------------------------


def test_direct_comparison_lists_each_field_per_paper(corpus: Path) -> None:
  """Each compared field carries one column of statements per selected Paper."""
  result = _build(corpus, ["p01", "p02"])

  assert not result.is_chunked
  assert result.unit_labels == ["Study p01 (2020)", "Study p02 (2020)"]
  by_field = {field.field_id: field for field in result.fields}
  assert [f.label for f in result.fields] == [
    "Research question",
    "Methods",
    "Population",
    "Datasets",
    "Findings",
    "Limitations",
  ]
  methods = by_field["methods"]
  assert methods.units[0].statements == ["p01 used a study design."]
  assert methods.units[1].statements == ["p02 used a study design."]


def test_direct_comparison_highlights_agreements_and_differences(corpus: Path) -> None:
  """Papers agree on a category all share and differ on one only some hold."""
  result = _build(corpus, ["p01", "p02"])
  axes = {axis.axis: axis for axis in result.axes}

  method = axes["method"]
  assert [c.label for c in method.agreements] == ["Retrospective Studies"]
  assert method.differences == []

  population = axes["population"]
  # Only p01 is placed on Aged, so it is a difference, not an agreement.
  assert [c.label for c in population.agreements] == []
  assert [(c.label, c.covering_keys) for c in population.differences] == [
    ("Aged", ["p01"])
  ]


def test_direct_comparison_reports_selection_blind_spots(corpus: Path) -> None:
  """The mini matrix lists Corpus categories none of the selection covers."""
  result = _build(corpus, ["p01", "p02"])
  axes = {axis.axis: axis for axis in result.axes}

  assert result.corpus_paper_count == 6
  # p01/p02 are takotsubo only, retrospective only, and touch no dataset.
  assert [c.label for c in axes["topic"].blind_spots] == ["Heart Failure"]
  assert [c.label for c in axes["method"].blind_spots] == [
    "Randomized Controlled Trial"
  ]
  assert [c.label for c in axes["dataset"].blind_spots] == ["Electronic Health Records"]
  # Aged is covered by p01, so the population axis has no blind spot.
  assert axes["population"].blind_spots == []


# -- Summarize-then-compare (6-15 Papers) ---------------------------------


def test_large_selection_is_chunked_into_sets_of_at_most_five(corpus: Path) -> None:
  """Six Papers split into two summarized sets that are compared, not the Papers."""
  result = _build(corpus, list(_PLACEMENTS))

  assert result.is_chunked
  assert [chunk.citation_keys for chunk in result.chunks] == [
    ["p01", "p02", "p03"],
    ["p04", "p05", "p06"],
  ]
  assert result.unit_labels == [
    "Set 1 (p01, p02, p03)",
    "Set 2 (p04, p05, p06)",
  ]


def test_chunked_fields_combine_each_set_and_tag_the_source_paper(
  corpus: Path,
) -> None:
  """A set's field statements union its Papers', each tagged with its key."""
  result = _build(corpus, list(_PLACEMENTS))
  methods = next(f for f in result.fields if f.field_id == "methods")

  assert methods.units[0].statements == [
    "[p01] p01 used a study design.",
    "[p02] p02 used a study design.",
    "[p03] p03 used a study design.",
  ]


def test_chunked_axes_compare_the_set_summaries(corpus: Path) -> None:
  """Agreement is over set summaries: both sets study takotsubo and heart failure."""
  result = _build(corpus, list(_PLACEMENTS))
  topic = next(axis for axis in result.axes if axis.axis == "topic")

  # Set 1 (p01,p02,p03) and Set 2 (p04,p05,p06) each cover both topics.
  assert {c.label for c in topic.agreements} == {
    "Takotsubo Cardiomyopathy",
    "Heart Failure",
  }
  # Every corpus topic is covered by some set, so no topic blind spot remains.
  assert topic.blind_spots == []


# -- Streamlit page --------------------------------------------------------


def test_comparison_page_renders_a_selection(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The Comparison page runs, and once Papers are picked it shows the sections."""
  from streamlit.testing.v1 import AppTest

  # Keep the corpora dir set across every rerun (each interaction reruns the app).
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpus.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()

  app.sidebar.radio[0].set_value("Paper Comparison").run()
  assert not app.exception

  app.multiselect[0].set_value(["p01", "p02"]).run()
  assert not app.exception
  subheaders = [element.value for element in app.subheader]
  assert "Field by field" in subheaders
  assert any("none of the selection covers" in value for value in subheaders)
