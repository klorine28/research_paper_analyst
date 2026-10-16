"""
Behavior of the saturate stage (ADR 0009): try to disprove one Candidate Gap.

The fixture Corpus (`fixtures/saturation-corpus`, built and recorded by
`scripts/record_saturation_fixture.py`) holds a Knowledge Gap the wider
literature plainly fills and a control Coverage Gap nothing closes, plus an
Unanswered Limitation later work followed up and a control limitation nobody
did. Searches are
answered from responses recorded from the live OpenAlex and PubMed APIs, keyed
by URL, so the real adapters run and nothing goes to the network.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from research_gap_dashboard.aggregate import (
  AggregateReport,
  CategoryAssignment,
  NormalizedFacts,
  load_pipeline_taxonomies,
)
from research_gap_dashboard.detect import StatementEvidence, UnansweredLimitationGap
from research_gap_dashboard.extract import Evidence
from research_gap_dashboard.cli import EXIT_ERROR, EXIT_INCOMPLETE, EXIT_OK, main
from research_gap_dashboard import cli
from research_gap_dashboard.saturate import (
  OUT_OF_CORPUS_LABEL,
  SATURATION_CHECKS_NAME,
  ConceptTerms,
  SaturationError,
  build_query,
  limitation_concepts,
  read_saturation_checks,
  saturate_gap,
  screen,
)
from research_gap_dashboard.sources import OpenAlexAdapter, PubMedAdapter, WorkRecord
from research_gap_dashboard.taxonomy import SEED_PATHS

FIXTURE = Path(__file__).parent / "fixtures" / "saturation-corpus"
RECORDED = json.loads((FIXTURE / "source-responses.json").read_text(encoding="utf-8"))
SEARCH_LIMIT = 10  # the limit the responses were recorded with
FILLED_GAP = "topicxtopic:heart-failure:myocardial-ischemia"
CONTROL_GAP = "topicxmethod:carcinoid-heart-disease:mendelian-randomization-analysis"
FILLED_LIMITATION = "limitation:limitation-group-01"
CONTROL_LIMITATION = "limitation:limitation-group-02"
TAXONOMIES = load_pipeline_taxonomies(list(SEED_PATHS.values()))


def _recorded_json(url: str) -> dict[str, Any] | None:
  """Answer a JSON fetch from the recorded responses, or fail loudly."""
  assert url in RECORDED["json"], f"No recorded response for {url}"
  return RECORDED["json"][url]


def _recorded_text(url: str) -> str | None:
  """Answer a text fetch from the recorded responses, or fail loudly."""
  assert url in RECORDED["text"], f"No recorded response for {url}"
  return RECORDED["text"][url]


def _recorded_sources() -> list[Any]:
  """Build the real OpenAlex and PubMed adapters over the recorded responses."""
  return [
    OpenAlexAdapter(fetch=_recorded_json),
    PubMedAdapter(fetch=_recorded_json, fetch_text=_recorded_text),
  ]


def _offline(_url: str) -> Any:
  """Fail like a fetch with no network."""
  raise httpx.ConnectError("network is unreachable")


@pytest.fixture(name="corpus")
def corpus_fixture(tmp_path: Path) -> Path:
  """Copy the saturation fixture Corpus somewhere writable."""
  return Path(shutil.copytree(FIXTURE, tmp_path / "saturation-corpus"))


def _check(corpus: Path, gap_id: str, sources: list[Any] | None = None, **kwargs):
  """Run one saturation check on the fixture with the recorded sources."""
  return saturate_gap(
    corpus,
    gap_id,
    _recorded_sources() if sources is None else sources,
    TAXONOMIES,
    limit=SEARCH_LIMIT,
    **kwargs,
  )


def test_a_known_filled_gap_is_possibly_addressed(corpus: Path):
  """Hits mentioning both concepts make the gap "possibly addressed"."""
  check = _check(corpus, FILLED_GAP)

  assert check.status == "possibly_addressed"
  assert check.screened_hit_count > 0
  assert f"{check.screened_hit_count} screened hit(s)" in check.status_reason
  assert {s.source for s in check.searches} == {"openalex", "pubmed"}


def test_the_control_gap_records_what_was_searched(corpus: Path):
  """No closing work found is a dated record of sources and queries, not a verdict."""
  moment = datetime(2026, 10, 4, 9, 30, tzinfo=UTC)
  check = _check(corpus, CONTROL_GAP, now=lambda: moment)

  assert check.status == "no_closing_work_found"
  assert check.checked_at == moment
  assert "does not confirm" in check.status_reason
  assert "openalex" in check.status_reason
  assert "pubmed" in check.status_reason
  for search in check.searches:
    assert "Carcinoid Heart Disease" in search.query
    assert "mendelian randomization" in search.query
  # The search did return works; screening is what found none closing the gap.
  assert check.hits
  assert all(hit.verdict == "missing_concept" for hit in check.hits)


def test_the_result_never_confirms_a_gap_or_changes_confidence(corpus: Path):
  """A check annotates; the CandidateGaps artifact is left untouched."""
  gaps_path = corpus / "artifacts" / "candidate_gaps.json"
  before = gaps_path.read_text(encoding="utf-8")

  checks = [_check(corpus, FILLED_GAP), _check(corpus, CONTROL_GAP)]

  assert gaps_path.read_text(encoding="utf-8") == before
  for check in checks:
    assert check.status in {"possibly_addressed", "no_closing_work_found"}
    assert "confirmed" not in check.status_reason.lower()


def test_hits_are_labelled_out_of_corpus_and_exclude_corpus_papers(corpus: Path):
  """The log carries the out-of-Corpus label, and a Corpus Paper is never a hit."""
  manifest_path = corpus / "artifacts" / "corpus-manifest.json"
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  first = next(hit for hit in _check(corpus, FILLED_GAP).hits if hit.doi)
  manifest["papers"][0]["doi"] = first.doi
  manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

  check = _check(corpus, FILLED_GAP)

  assert first.work_id not in {hit.work_id for hit in check.hits}
  assert any(first.work_id in s.in_corpus_work_ids for s in check.searches)
  assert read_saturation_checks(corpus).label == OUT_OF_CORPUS_LABEL


def test_snapshots_are_append_only_and_dated(corpus: Path):
  """Each check adds a dated entry; earlier entries are kept byte for byte."""
  times = iter([datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)])
  _check(corpus, CONTROL_GAP, now=lambda: next(times))
  first = read_saturation_checks(corpus).checks[0].model_dump()
  _check(corpus, CONTROL_GAP, now=lambda: next(times))

  log = read_saturation_checks(corpus)
  assert [c.check_id for c in log.checks] == ["check-0001", "check-0002"]
  assert log.checks[0].model_dump() == first
  assert [c.checked_at.month for c in log.checks] == [1, 2]
  assert (corpus / "artifacts" / SATURATION_CHECKS_NAME).is_file()


def test_offline_marks_the_check_not_run(corpus: Path):
  """When no source can be reached the check completes as "not run"."""
  sources = [
    OpenAlexAdapter(fetch=_offline),
    PubMedAdapter(fetch=_offline, fetch_text=_offline),
  ]

  check = _check(corpus, CONTROL_GAP, sources=sources)

  assert check.status == "not_run"
  assert "ConnectError" in check.status_reason
  assert all(search.error for search in check.searches)
  assert not check.hits


def test_one_failing_source_still_counts_the_other(corpus: Path):
  """A source that fails is recorded; the result rests on those that ran."""
  sources = [OpenAlexAdapter(fetch=_recorded_json), PubMedAdapter(fetch=_offline)]

  check = _check(corpus, FILLED_GAP, sources=sources)

  assert check.status == "possibly_addressed"
  pubmed = next(s for s in check.searches if s.source == "pubmed")
  assert pubmed.error
  assert "pubmed" not in check.status_reason


def test_an_unknown_gap_is_refused(corpus: Path):
  """Only a gap the CandidateGaps artifact holds can be checked."""
  with pytest.raises(SaturationError, match="No Candidate Gap"):
    _check(corpus, "topicxtopic:nope:nada")


def test_queries_come_from_labels_aliases_and_corpus_terms():
  """PubMed gets MeSH and title/abstract tags; OpenAlex gets quoted phrases."""
  concepts = [
    ConceptTerms(
      axis="topic",
      category_id="heart-failure",
      label="Heart Failure",
      terms=["Heart Failure", "Cardiac Failure"],
      mesh_heading="Heart Failure",
    ),
    ConceptTerms(
      axis="population", category_id="aged", label="Aged", terms=["Aged", "Elderly"]
    ),
  ]

  assert build_query("openalex", concepts) == (
    '("Heart Failure" OR "Cardiac Failure") AND ("Aged" OR "Elderly")'
  )
  assert build_query("pubmed", concepts) == (
    '("Heart Failure"[MeSH Terms] OR "Heart Failure"[tiab] OR '
    '"Cardiac Failure"[tiab]) AND ("Aged"[tiab] OR "Elderly"[tiab])'
  )


def test_inverted_mesh_aliases_are_searched_in_natural_order(corpus: Path):
  """'Heart Failure, Congestive' is searched as 'Congestive Heart Failure'."""
  check = _check(corpus, FILLED_GAP)

  heart_failure = check.concepts[0]
  assert "Congestive Heart Failure" in heart_failure.terms
  assert not any("," in term for term in heart_failure.terms)
  assert "chronic heart failure" in heart_failure.terms  # the Corpus's own term


@pytest.mark.parametrize(
  ("title", "abstract", "verdict"),
  [
    ("Cardiac failures after myocardial-ischemia", "", "mentions_both"),
    ("Heart failure outcomes", "In ischemic heart disease patients.", "mentions_both"),
    ("Heart failure outcomes", "Nothing else.", "missing_concept"),
    ("Heartfailure and myocardial ischemia", "", "missing_concept"),
  ],
)
def test_screening_needs_both_concepts_in_title_or_abstract(
  title: str, abstract: str, verdict: str
):
  """A hit counts only when a term of each concept appears as whole words."""
  concepts = [
    ConceptTerms(
      axis="topic",
      category_id="hf",
      label="Heart Failure",
      terms=["Heart Failure", "Cardiac Failure"],
    ),
    ConceptTerms(
      axis="topic",
      category_id="mi",
      label="Myocardial Ischemia",
      terms=["Myocardial Ischemia", "Ischemic Heart Disease"],
    ),
  ]
  record = WorkRecord(doi="10.1/x", title=title, abstract=abstract)

  assert screen(record, concepts)[0] == verdict


# --- The CLI -------------------------------------------------------------------


def test_cli_saturate_checks_one_gap(corpus: Path, monkeypatch: pytest.MonkeyPatch):
  """`saturate <corpus> --gap <id>` runs the check and appends the snapshot."""
  monkeypatch.setattr(cli, "_build_search_sources", _recorded_sources)
  monkeypatch.setattr(cli, "SATURATE_SEARCH_LIMIT", SEARCH_LIMIT)

  assert main(["saturate", str(corpus), "--gap", FILLED_GAP]) == EXIT_OK
  assert read_saturation_checks(corpus).checks[0].status == "possibly_addressed"


def test_cli_saturate_offline_is_incomplete(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """Offline, the command still completes, signalling the check did not run."""
  monkeypatch.setattr(
    cli, "_build_search_sources", lambda: [OpenAlexAdapter(fetch=_offline)]
  )

  assert main(["saturate", str(corpus), "--gap", FILLED_GAP]) == EXIT_INCOMPLETE
  assert read_saturation_checks(corpus).checks[0].status == "not_run"


def test_cli_saturate_rejects_an_unknown_gap(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """An unknown gap id is an error, and no snapshot is written."""
  monkeypatch.setattr(cli, "_build_search_sources", _recorded_sources)

  assert main(["saturate", str(corpus), "--gap", "nope"]) == EXIT_ERROR
  assert not read_saturation_checks(corpus).checks


class _FakeSource:  # pylint: disable=too-few-public-methods
  """A topic-search source that returns fixed records."""

  def __init__(self, name: str, records: list[WorkRecord]):
    self.name = name
    self._records = records

  def search(self, query: str, limit: int = 25) -> list[WorkRecord]:
    """Return the fixed records, whatever the query."""
    assert query
    return self._records[:limit]


def test_a_work_two_sources_return_is_one_hit(corpus: Path):
  """OpenAlex and PubMed records sharing a PMID merge into one hit."""
  title = "Heart failure after myocardial ischemia"
  sources = [
    _FakeSource("openalex", [WorkRecord(doi="10.1/both", pmid="42", title=title)]),
    _FakeSource("pubmed", [WorkRecord(doi="", pmid="42", title=title)]),
  ]

  check = _check(corpus, FILLED_GAP, sources=sources)

  assert len(check.hits) == 1
  assert check.hits[0].sources == ["openalex", "pubmed"]
  assert check.hits[0].doi == "10.1/both"


# --- Unanswered Limitations (issue #88) -------------------------------------------


def test_a_followed_up_limitation_is_possibly_addressed(corpus: Path):
  """Later work on a limitation's subject and key phrases weakens the card."""
  check = _check(corpus, FILLED_LIMITATION)

  assert check.status == "possibly_addressed"
  assert check.gap_type == "unanswered_limitation"
  assert check.statement_ids == ["sat01:future_work[0]"]
  assert check.screened_hit_count > 0
  for search in check.searches:
    assert "Heart Failure" in search.query
    assert "preserved ejection fraction" in search.query
    assert "sglt2 inhibitors" in search.query


def test_a_limitation_query_comes_from_its_source_statement(corpus: Path):
  """The subject Topic and the statement's key phrases, never its boilerplate."""
  check = _check(corpus, FILLED_LIMITATION)

  subject, phrases = check.concepts
  assert (subject.axis, subject.category_id) == ("topic", "heart-failure")
  assert subject.mesh_heading == "Heart Failure"
  assert phrases.axis == "statement"
  assert phrases.terms == ["preserved ejection fraction", "sglt2 inhibitors"]
  for word in ("investigated", "remains", "whether", "patients"):
    assert all(word not in term.split() for term in phrases.terms)


def test_work_published_before_the_statement_is_no_follow_up(corpus: Path):
  """A hit older than the source Paper is kept and shown, but never counted."""
  check = _check(corpus, FILLED_LIMITATION)

  older = [hit for hit in check.hits if hit.year is not None and hit.year < 2020]
  assert older
  assert all(hit.verdict != "mentions_both" for hit in older)
  assert any(hit.verdict == "predates_statement" for hit in older)
  assert all(
    hit.year is None or hit.year >= 2020
    for hit in check.hits
    if hit.verdict == "mentions_both"
  )
  assert "no earlier than" in check.screening_method


def test_the_control_limitation_records_what_was_searched(corpus: Path):
  """No follow-up found is a dated record of the queries, not a verdict."""
  moment = datetime(2026, 10, 4, 9, 30, tzinfo=UTC)
  check = _check(corpus, CONTROL_LIMITATION, now=lambda: moment)

  assert check.status == "no_closing_work_found"
  assert check.checked_at == moment
  assert "does not confirm" in check.status_reason
  for search in check.searches:
    assert "Carcinoid Heart Disease" in search.query
    assert "spaceflight" in search.query


def test_a_limitation_check_leaves_the_card_untouched(corpus: Path):
  """Checking a limitation annotates only; its confidence is unchanged."""
  gaps_path = corpus / "artifacts" / "candidate_gaps.json"
  before = gaps_path.read_text(encoding="utf-8")

  _check(corpus, FILLED_LIMITATION)

  assert gaps_path.read_text(encoding="utf-8") == before
  assert read_saturation_checks(corpus).label == OUT_OF_CORPUS_LABEL


def test_a_retrieval_gap_is_never_checked(corpus: Path):
  """A Retrieval Gap is already out of the Corpus: refused, nothing written."""
  retrieval = {
    "corpus_root": str(corpus),
    "source": "openalex",
    "corpus_paper_count": 6,
    "coupled_paper_count": 6,
    "min_overlap": 2,
    "limit": 10,
    "candidates": [
      {
        "openalex_id": "https://openalex.org/W123",
        "doi": "10.1/cited",
        "citation_overlap": 3,
        "citing_citation_keys": ["sat01", "sat02", "sat03"],
      }
    ],
  }
  (corpus / "artifacts" / "retrieval_gaps.json").write_text(
    json.dumps(retrieval), encoding="utf-8"
  )

  for gap_id in ("https://openalex.org/W123", "W123", "10.1/cited"):
    with pytest.raises(SaturationError, match="Retrieval Gap"):
      _check(corpus, gap_id)
  assert not read_saturation_checks(corpus).checks


def _limitation(statement: str) -> UnansweredLimitationGap:
  """Build a one-statement Unanswered Limitation card."""
  return UnansweredLimitationGap(
    gap_id="limitation:g",
    gap_type="unanswered_limitation",
    title=statement,
    explanation="",
    confidence="low",
    confidence_reason="",
    group_id="g",
    source_citation_keys=["p1"],
    later_paper_count=0,
    corpus_paper_count=2,
    evidence=[
      StatementEvidence(
        citation_key="p1",
        statement_id="p1:limitations[0]",
        statement=statement,
        evidence=Evidence(passage=statement, section="discussion"),
      )
    ],
  )


def _normalized(*placements: tuple[str, str, str]) -> AggregateReport:
  """NormalizedFacts placing one Paper per (key, Topic id, own term)."""
  return AggregateReport(
    corpus_root=Path("c"),
    prompt_version="t",
    tier="default",
    taxonomies=[],
    normalized=[
      NormalizedFacts(
        citation_key=key,
        assignments=[
          CategoryAssignment(
            axis="topic",
            original_term=term,
            category_id=category,
            category_label=category,
            fact_ref="topics[0]",
            evidence=Evidence(passage=term, section="abstract"),
          )
        ],
        unmapped=[],
      )
      for key, category, term in placements
    ],
    failures=[],
  )


def test_a_corpus_acronym_finds_the_subject_but_is_never_searched():
  """'TTS' names Takotsubo through the Corpus's own term; the query spells it out."""
  normalized = _normalized(("p1", "takotsubo-cardiomyopathy", "TTS"))

  subject, phrases = limitation_concepts(
    _limitation("The role of estrogen in TTS remains to be elucidated."),
    TAXONOMIES,
    normalized,
  )

  assert subject.category_id == "takotsubo-cardiomyopathy"
  assert "Takotsubo Cardiomyopathy" in subject.terms
  assert "TTS" not in subject.terms
  assert phrases.terms == ["estrogen"]


def test_a_parent_alias_inside_a_child_term_is_not_a_second_subject():
  """'takotsubo cardiomyopathy' is one Topic, not also Cardiomyopathies."""
  concepts = limitation_concepts(
    _limitation("Genetic predisposition to takotsubo cardiomyopathy is unclear."),
    TAXONOMIES,
    None,
  )

  assert [c.category_id for c in concepts] == [
    "takotsubo-cardiomyopathy",
    "statement-key-phrases",
  ]
  assert concepts[-1].terms == ["genetic predisposition"]


def test_a_statement_naming_no_topic_searches_the_corpus_dominant_topic():
  """Without a named Topic, the Corpus's most-placed Topic keeps the search on field."""
  normalized = _normalized(
    ("p1", "heart-failure", "heart failure"),
    ("p2", "heart-failure", "heart failure"),
    ("p3", "myocarditis", "myocarditis"),
  )

  concepts = limitation_concepts(
    _limitation("Biomarker kinetics were not measured."), TAXONOMIES, normalized
  )

  assert concepts[0].category_id == "heart-failure"
  assert concepts[-1].terms == ["biomarker kinetics", "measured"]


@pytest.mark.parametrize(
  "statement",
  [
    "Further studies of carcinoid heart disease are needed.",
    "Only female carcinoid heart disease patients were included.",
  ],
)
def test_a_limitation_with_nothing_searchable_is_refused(corpus: Path, statement: str):
  """All subject, boilerplate and weak lone words: nothing to search for."""
  path = corpus / "artifacts" / "candidate_gaps.json"
  report = json.loads(path.read_text(encoding="utf-8"))
  card = next(g for g in report["gaps"] if g["gap_id"] == CONTROL_LIMITATION)
  card["evidence"][0]["statement"] = (
    "Further studies of carcinoid heart disease are needed."
  )
  path.write_text(json.dumps(report), encoding="utf-8")

  with pytest.raises(SaturationError, match="no searchable term"):
    _check(corpus, CONTROL_LIMITATION)


def test_cli_saturate_checks_a_limitation(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """`saturate <corpus> --gap limitation:<group>` runs the same check."""
  monkeypatch.setattr(cli, "_build_search_sources", _recorded_sources)
  monkeypatch.setattr(cli, "SATURATE_SEARCH_LIMIT", SEARCH_LIMIT)

  assert main(["saturate", str(corpus), "--gap", FILLED_LIMITATION]) == EXIT_OK
  check = read_saturation_checks(corpus).checks[0]
  assert (check.gap_id, check.status) == (FILLED_LIMITATION, "possibly_addressed")
