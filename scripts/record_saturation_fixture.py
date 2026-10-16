"""
Build the saturation fixture Corpus and record live OpenAlex and PubMed answers.

Run manually (`uv run scripts/record_saturation_fixture.py`) when the fixture
must be rebuilt; the test suite never touches the network. It writes
`tests/fixtures/saturation-corpus/`: six synthetic Papers placed so that `detect`
cards two cells the tests check (issue #85):

- Heart Failure x Myocardial Ischemia, a Knowledge Gap the wider literature has
  plainly filled, so a saturation check must report it possibly addressed;
- Carcinoid Heart Disease x Mendelian Randomization Analysis, a Coverage Gap
  whose searches return nothing that mentions both concepts (the control).

Every search the two checks make is recorded, trimmed to the fields the
adapters read, in `source-responses.json`, keyed by the exact URL. The adapters
are built without `OPENALEX_MAILTO` or `NCBI_API_KEY`, so no credential is
ever written into a recorded URL.
"""

import json
import logging
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from research_gap_dashboard.aggregate import (
  AggregateReport,
  CategoryAssignment,
  NormalizedFacts,
  load_pipeline_taxonomies,
)
from research_gap_dashboard.detect import detect_corpus
from research_gap_dashboard.extract import Evidence, ExtractReport
from research_gap_dashboard.ingest import CorpusManifest, Paper
from research_gap_dashboard.saturate import saturate_gap
from research_gap_dashboard.sources import (
  OpenAlexAdapter,
  PubMedAdapter,
  _httpx_fetch,
  _httpx_fetch_text,
)
from research_gap_dashboard.taxonomy import SEED_PATHS

logger = logging.getLogger("record_saturation_fixture")

ROOT = Path("tests/fixtures/saturation-corpus")
SEARCH_LIMIT = 10
FILLED_GAP = "topicxtopic:heart-failure:myocardial-ischemia"
CONTROL_GAP = "topicxmethod:carcinoid-heart-disease:mendelian-randomization-analysis"

# (axis, category id, label, the Paper's own term) per synthetic Paper.
PLACEMENTS: dict[str, list[tuple[str, str, str, str]]] = {
  "sat01": [
    ("topic", "heart-failure", "Heart Failure", "chronic heart failure"),
    ("method", "cohort-studies", "Cohort Studies", "cohort study"),
  ],
  "sat02": [
    ("topic", "heart-failure", "Heart Failure", "heart failure"),
    (
      "method",
      "mendelian-randomization-analysis",
      "Mendelian Randomization Analysis",
      "mendelian randomization",
    ),
  ],
  "sat03": [
    ("topic", "myocardial-ischemia", "Myocardial Ischemia", "myocardial ischaemia"),
    ("method", "cohort-studies", "Cohort Studies", "cohort study"),
  ],
  "sat04": [
    ("topic", "myocardial-ischemia", "Myocardial Ischemia", "myocardial ischemia"),
    (
      "method",
      "randomized-controlled-trial",
      "Randomized Controlled Trial",
      "randomised trial",
    ),
  ],
  "sat05": [
    ("topic", "carcinoid-heart-disease", "Carcinoid Heart Disease", "carcinoid heart"),
    ("method", "cohort-studies", "Cohort Studies", "cohort study"),
  ],
  "sat06": [
    ("topic", "carcinoid-heart-disease", "Carcinoid Heart Disease", "carcinoid heart"),
    ("method", "case-reports", "Case Reports", "case report"),
  ],
}


README = """# Saturation fixture Corpus

Six synthetic Papers (no PDFs) placed so `detect` cards a Knowledge Gap the
wider literature plainly fills (Heart Failure x Myocardial Ischemia) and a
control Coverage Gap nothing closes (Carcinoid Heart Disease x Mendelian
Randomization Analysis). `source-responses.json` holds the OpenAlex and PubMed
answers to both saturation checks, recorded live and keyed by URL, so the tests
run offline. Rebuild with `uv run scripts/record_saturation_fixture.py`.
"""


class _NoLlm:  # pylint: disable=too-few-public-methods
  """Detect only calls the LLM for limitations, and this Corpus states none."""

  def __getattr__(self, name: str) -> Any:
    raise AssertionError(f"The fixture Corpus must not need the LLM ({name}).")


def _write_corpus() -> None:
  """Write the manifest, empty Extractions, and NormalizedFacts, then detect."""
  if ROOT.exists():
    shutil.rmtree(ROOT)
  (ROOT / "artifacts").mkdir(parents=True)
  for name in ("papers", "paper-data", "judgments"):
    (ROOT / name).mkdir(parents=True)
    (ROOT / name / ".gitkeep").write_text("", encoding="utf-8")
  (ROOT / "corpus.bib").write_text("", encoding="utf-8")
  (ROOT / "README.md").write_text(README, encoding="utf-8")
  manifest = CorpusManifest(
    corpus_root=ROOT,
    papers=[
      Paper(
        citation_key=key,
        doi=f"10.5555/{key}",
        title=f"Synthetic fixture Paper {key}",
        pdf_path=Path("papers") / f"{key}.pdf",
        year=2020,
      )
      for key in PLACEMENTS
    ],
    unmatched_entries=[],
    orphan_pdfs=[],
  )
  normalized = AggregateReport(
    corpus_root=ROOT,
    prompt_version="fixture",
    tier="default",
    taxonomies=[],
    normalized=[
      NormalizedFacts(
        citation_key=key,
        assignments=[
          CategoryAssignment(
            axis=axis,  # type: ignore[arg-type]
            original_term=term,
            category_id=category,
            category_label=label,
            fact_ref=f"{axis}s[0]",
            evidence=Evidence(passage=f"We report a {term}.", section="methods"),
          )
          for axis, category, label, term in placements
        ],
        unmapped=[],
      )
      for key, placements in PLACEMENTS.items()
    ],
    failures=[],
  )
  extracted = ExtractReport(
    corpus_root=ROOT,
    prompt_version="fixture",
    tier="default",
    extractions=[],
    failures=[],
  )
  artifacts = ROOT / "artifacts"
  for name, model in (
    ("corpus-manifest.json", manifest),
    ("normalized_facts.json", normalized),
    ("extractions.json", extracted),
  ):
    (artifacts / name).write_text(model.model_dump_json(indent=2), encoding="utf-8")
  detect_corpus(ROOT, _NoLlm())  # type: ignore[arg-type]


def _trim_openalex(payload: dict[str, Any] | None) -> dict[str, Any] | None:
  """Keep only the search fields `OpenAlexAdapter.search` reads."""
  if payload is None:
    return None
  keep = (
    "id",
    "doi",
    "ids",
    "title",
    "publication_year",
    "cited_by_count",
    "abstract_inverted_index",
  )
  results = []
  for work in payload.get("results") or []:
    trimmed = {key: work.get(key) for key in keep}
    trimmed["authorships"] = [
      {"author": {"display_name": a.get("author", {}).get("display_name")}}
      for a in work.get("authorships") or []
    ]
    source = (work.get("primary_location") or {}).get("source") or {}
    trimmed["primary_location"] = {
      "source": {"display_name": source.get("display_name")}
    }
    results.append(trimmed)
  return {"results": results}


def _trim_pubmed_xml(body: str | None) -> str | None:
  """Keep only the efetch elements `PubMedAdapter.search` reads."""
  if body is None:
    return None
  root = ET.fromstring(body)  # noqa: S314
  out = ET.Element("PubmedArticleSet")
  for article in root.iter("PubmedArticle"):
    new = ET.SubElement(out, "PubmedArticle")
    citation = ET.SubElement(new, "MedlineCitation")
    pmid = article.find("MedlineCitation/PMID")
    if pmid is not None:
      citation.append(pmid)
    details = article.find("MedlineCitation/Article")
    new_details = ET.SubElement(citation, "Article")
    if details is not None:
      for path in ("Journal", "ArticleTitle", "Abstract", "AuthorList"):
        element = details.find(path)
        if element is not None:
          new_details.append(element)
      for location in details.iterfind("ELocationID"):
        new_details.append(location)
    ids = article.find("PubmedData/ArticleIdList")
    if ids is not None:
      ET.SubElement(new, "PubmedData").append(ids)
  return ET.tostring(out, encoding="unicode")


def main() -> None:
  """Rebuild the fixture Corpus and record both checks' responses."""
  logging.basicConfig(level=logging.INFO, format="%(message)s")
  _write_corpus()
  recorded: dict[str, dict[str, Any]] = {"json": {}, "text": {}}

  def fetch(url: str) -> dict[str, Any] | None:
    payload = _httpx_fetch(url)
    if "api.openalex.org" in url:
      payload = _trim_openalex(payload)
    recorded["json"][url] = payload
    return payload

  def fetch_text(url: str) -> str | None:
    body = _trim_pubmed_xml(_httpx_fetch_text(url))
    recorded["text"][url] = body
    return body

  sources = [
    OpenAlexAdapter(fetch=fetch),
    PubMedAdapter(fetch=fetch, fetch_text=fetch_text),
  ]
  taxonomies = load_pipeline_taxonomies(list(SEED_PATHS.values()))
  for gap_id in (FILLED_GAP, CONTROL_GAP):
    check = saturate_gap(ROOT, gap_id, sources, taxonomies, limit=SEARCH_LIMIT)
    logger.info("%s: %s", gap_id, check.status_reason)
  # The recorded checks are not part of the fixture: tests run their own.
  (ROOT / "artifacts" / "saturation_checks.json").unlink()
  (ROOT / "source-responses.json").write_text(
    json.dumps(recorded, indent=1, ensure_ascii=False), encoding="utf-8"
  )


if __name__ == "__main__":
  main()
