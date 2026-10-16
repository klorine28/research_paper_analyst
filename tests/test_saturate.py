"""
Behavior of the saturate stage (ADR 0009): try to disprove one cell gap.

The fixture Corpus (`fixtures/saturation-corpus`, built and recorded by
`scripts/record_saturation_fixture.py`) holds a Knowledge Gap the wider
literature plainly fills and a control Coverage Gap nothing closes. Searches are
answered from responses recorded from the live OpenAlex and PubMed APIs, keyed
by URL, so the real adapters run and nothing goes to the network. LLM screening
replays completions recorded live by `scripts/record_saturation_screening.py`.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from conftest import StubLlmClient

from research_gap_dashboard.aggregate import load_pipeline_taxonomies
from research_gap_dashboard.cli import EXIT_ERROR, EXIT_INCOMPLETE, EXIT_OK, main
from research_gap_dashboard import cli
from research_gap_dashboard.llm import (
  CachingLlmClient,
  DiskCache,
  LlmResponseError,
  RecordedLlmClient,
)
from research_gap_dashboard.saturate import (
  LLM_SCREEN_LIMIT,
  OUT_OF_CORPUS_LABEL,
  QUERY_VARIANT_PROMPT_VERSION,
  SATURATION_CHECKS_NAME,
  SCREENING_PROMPT_VERSION,
  ConceptTerms,
  HitScreener,
  QueryVariantModel,
  SaturationError,
  build_query,
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
TAXONOMIES = load_pipeline_taxonomies(list(SEED_PATHS.values()))
RECORDED_COMPLETIONS = FIXTURE / "llm-responses.json"
SCREENING_MODEL = "claude-haiku-4-5"  # the model the completions were recorded on


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
  monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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


# --- LLM screening of the top hits ---------------------------------------------


def _recorded_screener() -> HitScreener:
  """Screen with the completions recorded live on the fixture's filled gap."""
  client = RecordedLlmClient.from_file(RECORDED_COMPLETIONS)
  return HitScreener(client, model=SCREENING_MODEL, tier="cheap")


class _FailingLlmClient(StubLlmClient):  # pylint: disable=too-few-public-methods
  """An LlmClient whose every call fails like a response with no output."""

  def complete(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Count the call, then fail."""
    super().complete(*args, **kwargs)
    raise LlmResponseError("no tool_use block")


def test_the_top_hits_keep_their_llm_verdict_and_reason(corpus: Path):
  """The top title/abstract matches are LLM-screened; the snapshot keeps it all."""
  check = _check(corpus, FILLED_GAP, screener=_recorded_screener())

  saved = read_saturation_checks(corpus).checks[-1]
  matches = [hit for hit in saved.hits if hit.verdict == "mentions_both"]
  assert len(matches) > LLM_SCREEN_LIMIT
  for hit in matches[:LLM_SCREEN_LIMIT]:
    assert hit.llm_verdict in {"addresses", "partial", "no"}
    assert hit.llm_reason
  for hit in saved.hits[LLM_SCREEN_LIMIT:]:
    assert hit.llm_verdict is None
    assert hit.reason  # the deterministic verdict and reason are kept too
  assert saved == check


def test_llm_screening_records_its_provenance(corpus: Path):
  """The model, tier and prompt version behind the verdicts are on the check."""
  check = _check(corpus, FILLED_GAP, screener=_recorded_screener())

  run = check.llm_screening
  assert run is not None
  assert run.status == "screened"
  assert (run.model, run.tier) == (SCREENING_MODEL, "cheap")
  assert run.prompt_version == SCREENING_PROMPT_VERSION
  assert run.screened_count == LLM_SCREEN_LIMIT
  assert "LLM screening" in check.screening_method


def test_a_hit_the_llm_rules_out_stops_counting(corpus: Path):
  """Only hits the LLM did not judge "no" count towards "possibly addressed"."""
  check = _check(corpus, FILLED_GAP, screener=_recorded_screener())

  ruled_out = [hit for hit in check.hits if hit.llm_verdict == "no"]
  matches = [hit for hit in check.hits if hit.verdict == "mentions_both"]
  assert ruled_out
  assert check.screened_hit_count == len(matches) - len(ruled_out)
  assert check.status == "possibly_addressed"
  assert f"{check.screened_hit_count} screened hit(s)" in check.status_reason
  assert "LLM screening of the top 10" in check.status_reason
  assert "not LLM-screened" in check.status_reason


def test_when_the_llm_rules_out_every_match_no_closing_work_is_found(corpus: Path):
  """All matches judged "no": a dated "no closing work found", never "confirmed"."""
  title = "Heart failure after myocardial ischemia"
  sources = [_FakeSource("openalex", [WorkRecord(doi="10.1/one", title=title)])]
  client = StubLlmClient({"verdict": "no", "reason": "Mentions both in passing."})

  check = _check(
    corpus,
    FILLED_GAP,
    sources=sources,
    screener=HitScreener(client, model="m", tier="cheap"),
  )

  assert check.status == "no_closing_work_found"
  assert check.screened_hit_count == 0
  assert "does not confirm" in check.status_reason
  assert check.hits[0].llm_reason == "Mentions both in passing."


def test_without_an_llm_the_check_rests_on_title_and_abstract(corpus: Path):
  """No screener: LLM screening is recorded as not run; every match counts."""
  check = _check(corpus, FILLED_GAP)

  assert check.llm_screening is not None
  assert check.llm_screening.status == "not_run"
  assert "ANTHROPIC_API_KEY" in check.llm_screening.reason
  assert all(hit.llm_verdict is None for hit in check.hits)
  assert check.screened_hit_count == sum(
    1 for hit in check.hits if hit.verdict == "mentions_both"
  )


def test_failed_screening_calls_leave_hits_counting(corpus: Path):
  """A failing LLM never makes a gap look less addressed."""
  client = _FailingLlmClient({})
  baseline = _check(corpus, FILLED_GAP)

  check = _check(
    corpus, FILLED_GAP, screener=HitScreener(client, model="m", tier="cheap")
  )

  assert check.status == "possibly_addressed"
  assert check.screened_hit_count == baseline.screened_hit_count
  assert check.llm_screening is not None
  assert check.llm_screening.status == "not_run"
  assert len(check.llm_screening.errors) == LLM_SCREEN_LIMIT


def test_cli_saturate_screens_with_the_llm(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """With a key, the CLI screens on the cheap tier and records the usage."""
  monkeypatch.setattr(cli, "_build_search_sources", _recorded_sources)
  monkeypatch.setattr(cli, "SATURATE_SEARCH_LIMIT", SEARCH_LIMIT)
  monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
  monkeypatch.setenv("LLM_CHEAP_MODEL", SCREENING_MODEL)
  monkeypatch.setattr(
    cli,
    "build_llm_client",
    lambda **_: RecordedLlmClient.from_file(RECORDED_COMPLETIONS),
  )

  assert main(["saturate", str(corpus), "--gap", FILLED_GAP]) == EXIT_OK

  run = read_saturation_checks(corpus).checks[0].llm_screening
  assert run is not None
  assert (run.status, run.model, run.tier) == ("screened", SCREENING_MODEL, "cheap")


def test_cli_saturate_without_a_key_still_checks(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """No API key: the check runs on title/abstract screening, not an error."""


# --- The adversarial second attempt: LLM query variants on zero hits ------------


class _FakeVariantLlm:  # pylint: disable=too-few-public-methods
  """An LLM client that proposes fixed rephrasings and counts its calls."""

  name = "fake"

  def __init__(self, result: dict[str, Any] | Exception):
    self._result = result
    self.calls: list[dict[str, Any]] = []

  def complete(  # pylint: disable=unused-argument
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: str = "default",
    refresh: bool = False,
  ) -> dict[str, Any]:
    """Record the call, then answer with the fixed result (or raise it)."""
    self.calls.append({"prompt": prompt, "prompt_version": prompt_version})
    if isinstance(self._result, Exception):
      raise self._result
    return self._result


class _QuerySource:  # pylint: disable=too-few-public-methods
  """A topic-search source answering by which phrase the query contains."""

  def __init__(self, name: str, answers: dict[str, list[WorkRecord]]):
    self.name = name
    self._answers = answers
    self.queries: list[str] = []

  def search(self, query: str, limit: int = 25) -> list[WorkRecord]:
    """Return the records of the first phrase found in the query."""
    self.queries.append(query)
    for phrase, records in self._answers.items():
      if phrase in query:
        return records[:limit]
    return []


VARIANTS = {
  "first_concept_terms": ["carcinoid valve disease", "Carcinoid Heart Disease"],
  "second_concept_terms": ["genetic instrumental variable analysis"],
}
VARIANT_HIT = WorkRecord(
  doi="10.1/variant",
  title="Genetic instrumental variable analysis of carcinoid valve disease",
)


def _variant_check(corpus: Path, llm: Any, answers: dict[str, list[WorkRecord]]):
  """Check the control gap with fake sources and an LLM for the second attempt."""
  sources = [_QuerySource("openalex", answers), _QuerySource("pubmed", answers)]
  check = _check(
    corpus,
    CONTROL_GAP,
    sources=sources,
    query_variants=QueryVariantModel(client=llm, model="claude-test"),
  )
  return check, sources


def test_zero_hits_triggers_a_labelled_llm_second_attempt(corpus: Path):
  """With nothing found, LLM-rephrased queries run and are recorded verbatim."""
  llm = _FakeVariantLlm(VARIANTS)

  check, sources = _variant_check(
    corpus, llm, {"carcinoid valve disease": [VARIANT_HIT]}
  )

  assert len(llm.calls) == 1
  attempt = check.second_attempt
  assert attempt is not None
  assert attempt.model == "claude-test"
  assert attempt.prompt_version == QUERY_VARIANT_PROMPT_VERSION
  assert "second attempt" in attempt.label.lower()
  # Terms already searched are not "variants".
  assert attempt.concepts[0].terms == ["carcinoid valve disease"]
  variant_searches = [s for s in check.searches if s.attempt == "llm_variant"]
  assert {s.source for s in variant_searches} == {"openalex", "pubmed"}
  for search in variant_searches:  # a rephrasing, not a repeat of the base terms
    assert "carcinoid valve disease" in search.query
    assert '"carcinoid heart"' not in search.query
    assert "Carcinoid Heart Disease" not in search.query
  for search in check.searches:  # every query sent is recorded, verbatim
    assert search.query in {q for source in sources for q in source.queries}
  assert len(check.searches) == sum(len(source.queries) for source in sources)
  assert check.status == "possibly_addressed"
  assert "second attempt" in check.status_reason
  hit = next(h for h in check.hits if h.work_id == "10.1/variant")
  assert hit.attempt == "llm_variant"
  assert hit.verdict == "mentions_both"


def test_a_second_attempt_that_finds_nothing_still_says_so(corpus: Path):
  """No hits after both attempts is still only "no closing work found"."""
  check, _ = _variant_check(corpus, _FakeVariantLlm(VARIANTS), {})

  assert check.status == "no_closing_work_found"
  assert "second attempt" in check.status_reason
  assert "does not confirm" in check.status_reason


def test_the_llm_is_not_called_when_the_base_finds_closing_work(corpus: Path):
  """A gap the base queries already threaten never reaches the LLM."""
  llm = _FakeVariantLlm(VARIANTS)

  check = _check(
    corpus, FILLED_GAP, query_variants=QueryVariantModel(client=llm, model="m")
  )

  assert check.status == "possibly_addressed"
  assert not llm.calls
  assert check.second_attempt is None
  assert all(search.attempt == "base" for search in check.searches)


def test_the_llm_is_not_called_when_offline(corpus: Path):
  """When no source could be searched there is nothing to rephrase."""
  llm = _FakeVariantLlm(VARIANTS)
  sources = [OpenAlexAdapter(fetch=_offline)]

  check = _check(
    corpus,
    CONTROL_GAP,
    sources=sources,
    query_variants=QueryVariantModel(client=llm, model="m"),
  )

  assert check.status == "not_run"
  assert not llm.calls
  assert check.second_attempt is None


def test_without_an_llm_the_second_attempt_is_recorded_as_not_run(corpus: Path):
  """No configured LLM: the check completes and says the attempt did not run."""
  check = _check(corpus, CONTROL_GAP)

  assert check.status == "no_closing_work_found"
  assert check.second_attempt is not None
  assert check.second_attempt.note
  assert not check.second_attempt.concepts
  assert all(search.attempt == "base" for search in check.searches)


def test_a_failing_llm_leaves_the_base_result_with_a_note(corpus: Path):
  """An LLM error is recorded on the attempt; the check still completes."""
  llm = _FakeVariantLlm(LlmResponseError("no tool_use block"))

  check, _ = _variant_check(corpus, llm, {})

  assert check.status == "no_closing_work_found"
  assert check.second_attempt is not None
  assert "no tool_use block" in check.second_attempt.note
  assert all(search.attempt == "base" for search in check.searches)


def test_unusable_variants_run_no_search(corpus: Path):
  """Repeats, empty strings and long descriptions are dropped, not searched."""
  llm = _FakeVariantLlm(
    {
      "first_concept_terms": ["", "carcinoid heart", "a very long description of it"],
      "second_concept_terms": ["Mendelian Randomization Analysis"],
    }
  )

  check, _ = _variant_check(corpus, llm, {})

  assert check.second_attempt is not None
  assert check.second_attempt.note
  assert all(search.attempt == "base" for search in check.searches)


def test_variant_calls_are_cached_by_prompt_version(corpus: Path, tmp_path: Path):
  """A re-check with the same inputs is served from the cache, not the API."""
  inner = _FakeVariantLlm(VARIANTS)
  cached = CachingLlmClient(inner, DiskCache(tmp_path / "cache"))

  for _ in range(2):
    _variant_check(corpus, cached, {})

  assert len(inner.calls) == 1
  assert inner.calls[0]["prompt_version"].startswith(QUERY_VARIANT_PROMPT_VERSION)


def test_a_different_model_misses_the_variant_cache(corpus: Path, tmp_path: Path):
  """The model is part of the cache key: another model's terms are never reused."""
  inner = _FakeVariantLlm(VARIANTS)
  cached = CachingLlmClient(inner, DiskCache(tmp_path / "cache"))
  sources = [_QuerySource("openalex", {})]

  for model in ("model-a", "model-b"):
    check = _check(
      corpus,
      CONTROL_GAP,
      sources=sources,
      query_variants=QueryVariantModel(client=cached, model=model),
    )
    assert check.second_attempt is not None
    assert check.second_attempt.model == model

  assert len(inner.calls) == 2


def test_concepts_record_where_each_term_came_from(corpus: Path):
  """Each search term carries its origin: label, alias or Corpus term."""
  check = _check(corpus, FILLED_GAP)

  origins = check.concepts[0].term_origins
  assert origins["Heart Failure"] == "label"
  assert origins["Congestive Heart Failure"] == "alias"
  assert origins["chronic heart failure"] == "corpus_term"


def test_cli_saturate_runs_the_second_attempt_with_the_stage_llm(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """The CLI passes its cached LLM client and records the model it ran on."""
  llm = _FakeVariantLlm(VARIANTS)
  monkeypatch.setattr(
    cli,
    "_build_search_sources",
    lambda: [_QuerySource("openalex", {"carcinoid valve disease": [VARIANT_HIT]})],
  )
  monkeypatch.setattr(cli, "_llm_client", lambda _arguments: llm)
  monkeypatch.setenv("LLM_CHEAP_MODEL", "claude-cheap-test")

  assert main(["saturate", str(corpus), "--gap", CONTROL_GAP]) == EXIT_OK
  check = read_saturation_checks(corpus).checks[0]
  assert check.status == "possibly_addressed"
  assert check.second_attempt is not None
  assert check.second_attempt.model == "claude-cheap-test"


def test_cli_saturate_without_an_api_key_still_checks(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """No API key: the base check runs and the second attempt is noted as not run."""
  monkeypatch.setattr(cli, "_build_search_sources", _recorded_sources)
  monkeypatch.setattr(cli, "SATURATE_SEARCH_LIMIT", SEARCH_LIMIT)
  monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

  assert main(["saturate", str(corpus), "--gap", CONTROL_GAP]) == EXIT_OK
  check = read_saturation_checks(corpus).checks[0]
  assert check.status == "no_closing_work_found"
  assert check.second_attempt is not None
  assert "ANTHROPIC_API_KEY" in check.second_attempt.note
