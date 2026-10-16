"""
Automatic saturation checks after detect (ADR 0009 §2, issue #89).

`saturate --auto` checks the Corpus's high-confidence gaps, strongest first, at
most `AUTO_CHECK_CAP` per run, reusing any snapshot of the same queries younger
than the freshness window, and logs the rest as left for on-demand. Offline or
rate-limited runs still complete, with the checks recorded as not run. The
searches replay the saturation fixture's recorded OpenAlex and PubMed responses.
"""

import json
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from research_gap_dashboard import cli
from research_gap_dashboard.cli import EXIT_ERROR, EXIT_INCOMPLETE, EXIT_OK, main
from research_gap_dashboard.saturate import (
  AUTO_CHECKS_ENV,
  SaturationError,
  auto_checks_enabled,
  read_saturation_checks,
  saturate_auto,
  saturate_gap,
)
from research_gap_dashboard.sources import OpenAlexAdapter, PubMedAdapter
from research_gap_dashboard.taxonomy import SEED_PATHS
from research_gap_dashboard.aggregate import load_pipeline_taxonomies

FIXTURE = Path(__file__).parent / "fixtures" / "saturation-corpus"
RECORDED = json.loads((FIXTURE / "source-responses.json").read_text(encoding="utf-8"))
SEARCH_LIMIT = 10
FILLED_GAP = "topicxtopic:heart-failure:myocardial-ischemia"
CONTROL_GAP = "topicxmethod:carcinoid-heart-disease:mendelian-randomization-analysis"
FILLED_LIMITATION = "limitation:limitation-group-01"
LOW_GAP = "topicxtopic:carcinoid-heart-disease:heart-failure"
TAXONOMIES = load_pipeline_taxonomies(list(SEED_PATHS.values()))
T0 = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)


class _Fetch:
  """Answers fetches from the recorded responses, counting each call."""

  def __init__(self, fail: Exception | None = None) -> None:
    self.calls: list[str] = []
    self.fail = fail

  def json(self, url: str) -> dict[str, Any] | None:
    """Answer a JSON fetch."""
    self.calls.append(url)
    if self.fail is not None:
      raise self.fail
    assert url in RECORDED["json"], f"No recorded response for {url}"
    return RECORDED["json"][url]

  def text(self, url: str) -> str | None:
    """Answer a text fetch."""
    self.calls.append(url)
    if self.fail is not None:
      raise self.fail
    assert url in RECORDED["text"], f"No recorded response for {url}"
    return RECORDED["text"][url]

  def sources(self) -> list[Any]:
    """Build the real adapters over this fetch."""
    return [
      OpenAlexAdapter(fetch=self.json),
      PubMedAdapter(fetch=self.json, fetch_text=self.text),
    ]


def _rate_limited() -> httpx.HTTPStatusError:
  request = httpx.Request("GET", "https://api.openalex.org/works")
  response = httpx.Response(429, request=request)
  return httpx.HTTPStatusError(
    "429 Too Many Requests", request=request, response=response
  )


@pytest.fixture(name="corpus")
def corpus_fixture(tmp_path: Path) -> Path:
  """
  Copy the saturation fixture, with three high-confidence gaps.

  The filled gap is the strongest cell (lowest L1 probability), the control
  next; the followed-up limitation ranks after every cell gap.
  """
  root = Path(shutil.copytree(FIXTURE, tmp_path / "saturation-corpus"))
  path = root / "artifacts" / "candidate_gaps.json"
  report = json.loads(path.read_text(encoding="utf-8"))
  for gap in report["gaps"]:
    if gap["gap_id"] == FILLED_GAP:
      gap.update(confidence="high", l1_probability=0.01)
    elif gap["gap_id"] == CONTROL_GAP:
      gap.update(confidence="high", l1_probability=0.02)
    elif gap["gap_id"] == FILLED_LIMITATION:
      gap.update(confidence="high")
  path.write_text(json.dumps(report), encoding="utf-8")
  return root


def _auto(corpus: Path, fetch: _Fetch, *, at: datetime = T0, **kwargs: Any) -> Any:
  return saturate_auto(
    corpus,
    fetch.sources(),
    TAXONOMIES,
    limit=SEARCH_LIMIT,
    now=lambda: at,
    **kwargs,
  )


def _outcomes(run: Any) -> dict[str, str]:
  return {entry.gap_id: entry.outcome for entry in run.entries}


def test_only_high_confidence_gaps_are_checked_strongest_first(corpus: Path):
  """Every high gap is checked, cells by L1 first; a low gap is never touched."""
  run = _auto(corpus, _Fetch())

  checks = read_saturation_checks(corpus).checks
  assert [c.gap_id for c in checks] == [FILLED_GAP, CONTROL_GAP, FILLED_LIMITATION]
  assert {c.trigger for c in checks} == {"auto"}
  assert LOW_GAP not in _outcomes(run)
  assert checks[0].status == "possibly_addressed"
  assert checks[1].status == "no_closing_work_found"


def test_gaps_beyond_the_cap_are_left_for_on_demand(corpus: Path):
  """Over the cap, the weaker gaps are logged, not searched."""
  fetch = _Fetch()
  run = _auto(corpus, fetch, cap=1)

  assert _outcomes(run) == {
    FILLED_GAP: "checked",
    CONTROL_GAP: "left_for_on_demand",
    FILLED_LIMITATION: "left_for_on_demand",
  }
  assert run.left_for_on_demand == [CONTROL_GAP, FILLED_LIMITATION]
  log = read_saturation_checks(corpus)
  assert [c.gap_id for c in log.checks] == [FILLED_GAP]
  assert log.auto_runs == [run]
  assert run.cap == 1


def test_a_fresh_snapshot_of_the_same_queries_is_reused(corpus: Path):
  """Within 30 days a re-run searches nothing; after it, it checks again."""
  _auto(corpus, _Fetch())

  again = _Fetch()
  run = _auto(corpus, again, at=T0 + timedelta(days=29))

  assert not again.calls
  assert set(_outcomes(run).values()) == {"cached"}
  first = read_saturation_checks(corpus).checks
  assert [e.check_id for e in run.entries] == [c.check_id for c in first]

  later = _Fetch()
  run = _auto(corpus, later, at=T0 + timedelta(days=31))

  assert later.calls
  assert set(_outcomes(run).values()) == {"checked"}


def test_a_cached_gap_does_not_use_up_the_cap(corpus: Path):
  """A fresh manual check is reused, and the cap goes to the next gap."""
  saturate_gap(
    corpus,
    FILLED_GAP,
    _Fetch().sources(),
    TAXONOMIES,
    limit=SEARCH_LIMIT,
    now=lambda: T0,
  )

  run = _auto(corpus, _Fetch(), at=T0 + timedelta(days=1), cap=1)

  assert _outcomes(run) == {
    FILLED_GAP: "cached",
    CONTROL_GAP: "checked",
    FILLED_LIMITATION: "left_for_on_demand",
  }


def test_a_not_run_snapshot_is_never_reused(corpus: Path):
  """An offline result is not a cache hit: the next run searches again."""
  _auto(corpus, _Fetch(fail=httpx.ConnectError("network is unreachable")))

  fetch = _Fetch()
  run = _auto(corpus, fetch, at=T0 + timedelta(hours=1))

  assert fetch.calls
  assert set(_outcomes(run).values()) == {"checked"}


def test_offline_runs_complete_with_not_run_checks(corpus: Path):
  """Offline, every check is "not run (reason)"; each source is tried once."""
  fetch = _Fetch(fail=httpx.ConnectError("network is unreachable"))

  run = _auto(corpus, fetch)

  checks = read_saturation_checks(corpus).checks
  assert len(checks) == 3
  assert {c.status for c in checks} == {"not_run"}
  assert all("not run" in c.status_reason.lower() for c in checks)
  assert "ConnectError" in checks[0].status_reason
  assert "failed earlier in this run" in checks[1].status_reason
  assert len(fetch.calls) == 2  # one per source, then no more hammering
  assert {e.status for e in run.entries} == {"not_run"}


def test_rate_limited_runs_complete_with_not_run_checks(corpus: Path):
  """A 429 is recorded with its reason, and the source is not asked again."""
  fetch = _Fetch(fail=_rate_limited())

  _auto(corpus, fetch)

  checks = read_saturation_checks(corpus).checks
  assert {c.status for c in checks} == {"not_run"}
  assert "429" in checks[0].status_reason
  assert len(fetch.calls) == 2


@pytest.mark.parametrize(
  ("value", "enabled"),
  [(None, True), ("", True), ("on", True), ("1", True), ("off", False), ("0", False)],
)
def test_the_opt_out_setting(value: str | None, enabled: bool):
  """Automatic checks are on by default and can be switched off."""
  env = {} if value is None else {AUTO_CHECKS_ENV: value}
  assert auto_checks_enabled(env) is enabled


def test_an_unreadable_opt_out_setting_is_explained():
  """A typo is a clear error, not a silent default."""
  with pytest.raises(SaturationError, match=AUTO_CHECKS_ENV):
    auto_checks_enabled({AUTO_CHECKS_ENV: "maybe"})


def _patch_cli(monkeypatch: pytest.MonkeyPatch, fetch: _Fetch) -> None:
  monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
  monkeypatch.delenv(AUTO_CHECKS_ENV, raising=False)
  monkeypatch.setattr(cli, "_build_search_sources", fetch.sources)
  monkeypatch.setattr(cli, "SATURATE_SEARCH_LIMIT", SEARCH_LIMIT)


def test_cli_saturate_auto_checks_and_logs_the_rest(
  corpus: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
  """`saturate --auto` runs the checks and names what is left for on-demand."""
  caplog.set_level(logging.INFO)
  _patch_cli(monkeypatch, _Fetch())
  monkeypatch.setattr(cli, "AUTO_CHECK_CAP", 2)

  assert main(["saturate", str(corpus), "--auto"]) == EXIT_OK

  log = read_saturation_checks(corpus)
  assert len(log.checks) == 2
  assert log.auto_runs[0].left_for_on_demand == [FILLED_LIMITATION]
  assert FILLED_LIMITATION in caplog.text
  assert "on-demand" in caplog.text


def test_cli_saturate_auto_offline_is_incomplete(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """Offline, the command completes, signalling the checks did not run."""
  _patch_cli(monkeypatch, _Fetch(fail=httpx.ConnectError("offline")))

  assert main(["saturate", str(corpus), "--auto"]) == EXIT_INCOMPLETE


def test_cli_saturate_auto_switched_off_searches_nothing(
  corpus: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
  """With the opt-out set, the command does nothing and says so."""
  caplog.set_level(logging.INFO)
  fetch = _Fetch()
  _patch_cli(monkeypatch, fetch)
  monkeypatch.setenv(AUTO_CHECKS_ENV, "off")

  assert main(["saturate", str(corpus), "--auto"]) == EXIT_OK
  assert not fetch.calls
  assert not (corpus / "artifacts" / "saturation_checks.json").exists()
  assert AUTO_CHECKS_ENV in caplog.text


def test_cli_saturate_auto_with_a_bad_setting_fails(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """An unreadable opt-out setting stops the command before any search."""
  fetch = _Fetch()
  _patch_cli(monkeypatch, fetch)
  monkeypatch.setenv(AUTO_CHECKS_ENV, "perhaps")

  assert main(["saturate", str(corpus), "--auto"]) == EXIT_ERROR
  assert not fetch.calls


@pytest.mark.parametrize(
  "argv",
  [
    ["--auto", "--gap", FILLED_GAP],
    [],
  ],
)
def test_cli_saturate_needs_exactly_one_mode(
  corpus: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
):
  """`--gap` and `--auto` are alternatives, and one of them is required."""
  _patch_cli(monkeypatch, _Fetch())
  with pytest.raises(SystemExit):
    main(["saturate", str(corpus), *argv])
