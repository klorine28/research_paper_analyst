"""
Behavior of the dashboard's Corpus Overview: manifest artifact in, OverviewData out.

The overview is assembled by plain functions the layout layer calls; these tests
exercise that seam. They read the CorpusManifest artifact ingest writes, so a
schema drift between the writer and the dashboard's reader would surface here.
"""

import ast
import os
import shutil
from pathlib import Path

import pytest

import research_gap_dashboard.dashboard as dashboard_pkg

from research_gap_dashboard.dashboard.artifacts import (
  ArtifactNotFoundError,
  ManifestArtifact,
  discover_corpora,
  load_manifest,
)
from research_gap_dashboard.dashboard.overview import build_overview
from research_gap_dashboard.dashboard.text import SCOPE_STATEMENT, UNKNOWN_YEAR_LABEL
from research_gap_dashboard.ingest import ingest_corpus


@pytest.fixture(name="corpus")
def corpus_fixture(cardiology_corpus: Path, tmp_path: Path) -> Path:
  """Copy the fixture Corpus and run ingest so a manifest artifact exists."""
  target = tmp_path / "corpus"
  shutil.copytree(cardiology_corpus, target)
  ingest_corpus(target)
  return target


def test_load_manifest_reads_the_written_artifact(corpus: Path):
  """The dashboard loads the CorpusManifest ingest left on disk."""
  manifest = load_manifest(corpus)

  assert isinstance(manifest, ManifestArtifact)
  assert len(manifest.papers) == 10


def test_load_manifest_without_an_artifact_is_a_clear_error(tmp_path: Path):
  """Opening a corpus that was never ingested names the missing artifact."""
  with pytest.raises(ArtifactNotFoundError, match="ingest"):
    load_manifest(tmp_path)


def test_overview_counts_the_papers(corpus: Path):
  """The headline count is the number of Papers in the Corpus."""
  overview = build_overview(load_manifest(corpus))

  assert overview.paper_count == 10


def test_overview_buckets_papers_by_year(corpus: Path):
  """Years are counted, ascending, and the counts sum to the Corpus size."""
  overview = build_overview(load_manifest(corpus))

  labels = [bucket.label for bucket in overview.papers_per_year]
  assert labels == sorted(labels)
  assert sum(bucket.count for bucket in overview.papers_per_year) == 10
  assert overview.year_span == "2018\u20132021"


def test_overview_ranks_venues_by_paper_count(corpus: Path):
  """Venues are distinct journals, most-published first, and cover every Paper."""
  overview = build_overview(load_manifest(corpus))

  venues = {venue.venue: venue.count for venue in overview.venues}
  assert venues["Frontiers in Cardiovascular Medicine"] == 6
  assert venues["PLoS ONE"] == 4
  counts = [venue.count for venue in overview.venues]
  assert counts == sorted(counts, reverse=True)


def test_overview_carries_the_scope_statement(corpus: Path):
  """The candidates-not-verdicts statement travels with the overview."""
  overview = build_overview(load_manifest(corpus))

  assert overview.scope_statement == SCOPE_STATEMENT
  assert "not verdicts" in overview.scope_statement.lower()


def test_overview_has_no_exclusions_for_a_clean_corpus(corpus: Path):
  """A fully matched Corpus reports nothing left out."""
  overview = build_overview(load_manifest(corpus))

  assert not overview.has_exclusions
  assert overview.unmatched_entries == []
  assert overview.orphan_pdfs == []


def test_overview_reports_an_orphan_pdf(corpus: Path):
  """A PDF with no paper-list entry is surfaced, not silently dropped."""
  orphan = corpus / "papers" / "orphan-2099-10.9999_x.pdf"
  orphan.write_bytes(b"%PDF-1.4 stub")
  ingest_corpus(corpus)

  overview = build_overview(load_manifest(corpus))

  assert overview.has_exclusions
  assert orphan.name in overview.orphan_pdfs


def test_overview_handles_a_paper_with_an_unknown_year():
  """A Paper without a year lands in the Unknown bucket, kept last."""
  manifest = ManifestArtifact.model_validate(
    {
      "corpus_root": ".",
      "papers": [
        {"citation_key": "a", "doi": "10.1/a", "journal": "J", "year": 2020},
        {"citation_key": "b", "doi": "10.1/b", "journal": "J", "year": None},
      ],
    }
  )

  overview = build_overview(manifest)

  assert overview.papers_per_year[-1].label == UNKNOWN_YEAR_LABEL
  assert overview.papers_per_year[-1].count == 1


def test_discover_corpora_finds_ingested_corpora(corpus: Path):
  """Corpus selection lists directories that have a manifest to read."""
  choices = discover_corpora(corpus.parent)

  assert [choice.name for choice in choices] == [corpus.name]


def test_discover_corpora_ignores_directories_without_a_manifest(tmp_path: Path):
  """A directory that was never ingested is not offered for selection."""
  (tmp_path / "not-a-corpus").mkdir()

  assert not discover_corpora(tmp_path)


# The pipeline stage modules the dashboard must never import (ADR 0002): the
# front end only reads artifacts, so pulling any of these in is a design break.
_PIPELINE_MODULES = {
  "ingest",
  "parsing",
  "extract",
  "aggregate",
  "detect",
  "limitations",
  "taxonomy",
  "sources",
  "llm",
}


def _run_app(corpora_dir: Path):
  """Run the Streamlit app against a corpora folder and return the finished app."""
  from streamlit.testing.v1 import AppTest

  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30)
  os_environ_backup = os.environ.get("RESEARCH_GAP_CORPORA_DIR")
  os.environ["RESEARCH_GAP_CORPORA_DIR"] = str(corpora_dir)
  try:
    return app.run()
  finally:
    if os_environ_backup is None:
      os.environ.pop("RESEARCH_GAP_CORPORA_DIR", None)
    else:
      os.environ["RESEARCH_GAP_CORPORA_DIR"] = os_environ_backup


def test_app_runs_against_a_fixture_corpus(corpus: Path):
  """The Streamlit shell executes end to end and shows the overview."""
  app = _run_app(corpus.parent)

  assert not app.exception
  headers = [element.value for element in app.header]
  assert "Corpus Overview" in headers
  metric_values = [metric.value for metric in app.metric]
  assert "10" in metric_values
  assert any("not verdicts" in info.value.lower() for info in app.info)


def test_app_warns_when_no_corpus_is_analysed(tmp_path: Path):
  """With nothing ingested, the shell explains what to do rather than erroring."""
  app = _run_app(tmp_path)

  assert not app.exception
  assert any("ingest" in warning.value.lower() for warning in app.warning)


def test_dashboard_imports_no_pipeline_logic():
  """No module in the dashboard package imports a pipeline stage."""
  package_dir = Path(dashboard_pkg.__file__).parent
  offenders: list[str] = []
  for source in package_dir.glob("*.py"):
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
      names: list[str] = []
      if isinstance(node, ast.ImportFrom) and node.module:
        names.append(node.module)
      elif isinstance(node, ast.Import):
        names.extend(alias.name for alias in node.names)
      for name in names:
        leaf = name.split(".")[-1]
        if name.startswith("research_gap_dashboard") and leaf in _PIPELINE_MODULES:
          offenders.append(f"{source.name} imports {name}")
  assert not offenders
