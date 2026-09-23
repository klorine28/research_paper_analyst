# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""
Assemble the committed cardiology fixture Corpus from OpenAlex.

Run manually (`uv run --script scripts/fetch_fixture_corpus.py`) when the
fixture Corpus must be rebuilt; the test suite never touches the network.
Only CC-BY papers with a publisher-hosted PDF are kept, so the files can be
redistributed here, and each paper's licence and source URL land in
provenance.json next to the PDFs.
"""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("fetch_fixture_corpus")

OPENALEX_WORKS = "https://api.openalex.org/works"
CONTACT = "research-gap-dashboard (https://github.com/klorine28/research_paper_analyst)"
# Cardiology venues whose CC-BY PDFs are served without anti-bot blocking:
# Frontiers in Cardiovascular Medicine, PLOS ONE, BMC Cardiovascular Disorders.
VENUE_ISSNS = "2297-055X|1932-6203|1471-2261"
FILTERS = (
  "title.search:heart failure|atrial fibrillation|myocardial infarction,"
  f"best_oa_location.license:cc-by,primary_location.source.issn:{VENUE_ISSNS},"
  "type:article,from_publication_date:2018-01-01,to_publication_date:2024-12-31"
)
WANTED_PAPERS = 10
MAX_PDF_BYTES = 2_500_000
CORPUS_DIR = (
  Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "cardiology-corpus"
)
CONVENTION_DIRS = ("papers", "paper-data", "artifacts", "judgments")


def search(client: httpx.Client, per_page: int = 60) -> list[dict[str, Any]]:
  """Return OpenAlex works matching the fixture filters, most cited first."""
  response = client.get(
    OPENALEX_WORKS,
    params={
      "filter": FILTERS,
      "per_page": per_page,
      "sort": "cited_by_count:desc",
      "mailto": "research-gap-dashboard@example.org",
    },
    timeout=60,
  )
  response.raise_for_status()
  return response.json()["results"]


def doi_of(work: dict[str, Any]) -> str:
  """Return the bare DOI (no https://doi.org/ prefix) of a work."""
  return (work.get("doi") or "").removeprefix("https://doi.org/")


def citation_key(work: dict[str, Any]) -> str:
  """Build a stable BibTeX key: first author surname plus year."""
  authorships = work.get("authorships") or []
  name = authorships[0]["author"]["display_name"] if authorships else "anon"
  surname = re.sub(r"[^A-Za-z]", "", name.split(" ")[-1]) or "anon"
  return f"{surname.lower()}{work.get('publication_year', '0000')}"


def bibtex_entry(work: dict[str, Any], key: str) -> str:
  """Render one BibTeX article entry for an OpenAlex work."""
  authorships = work.get("authorships") or []
  source = (work.get("primary_location") or {}).get("source") or {}
  fields = {
    "title": (work.get("title") or "").rstrip("."),
    "author": " and ".join(a["author"]["display_name"] for a in authorships),
    "journal": source.get("display_name", ""),
    "year": str(work.get("publication_year", "")),
    "doi": doi_of(work),
  }
  body = ",\n".join(
    f"  {name} = {{{value}}}" for name, value in fields.items() if value
  )
  return f"@article{{{key},\n{body}\n}}\n"


def fetch_pdf(client: httpx.Client, url: str) -> bytes:
  """Download a PDF, failing loudly when the response is not one."""
  response = client.get(url, timeout=120, follow_redirects=True)
  response.raise_for_status()
  if not response.content.startswith(b"%PDF"):
    raise ValueError("response is not a PDF")
  return response.content


def prepare_corpus_dir() -> Path:
  """Create the corpus directories, empty, and return the papers directory."""
  for name in CONVENTION_DIRS:
    directory = CORPUS_DIR / name
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("*.pdf"):
      stale.unlink()
    (directory / ".gitkeep").touch()
  return CORPUS_DIR / "papers"


def main() -> int:
  """Fetch papers until the fixture Corpus is full, then write bib and provenance."""
  logging.basicConfig(level=logging.INFO, format="%(message)s")
  papers_dir = prepare_corpus_dir()

  entries: list[str] = []
  provenance: list[dict[str, Any]] = []
  with httpx.Client(headers={"User-Agent": CONTACT}) as client:
    for work in search(client):
      if len(provenance) >= WANTED_PAPERS:
        break
      location = work.get("best_oa_location") or {}
      pdf_url, doi = location.get("pdf_url"), doi_of(work)
      if not pdf_url or not doi or location.get("license") != "cc-by":
        continue
      key = citation_key(work)
      try:
        content = fetch_pdf(client, pdf_url)
      except (httpx.HTTPError, ValueError) as error:
        logger.warning("skipped %s: %s", doi, error)
        continue
      if len(content) > MAX_PDF_BYTES:
        logger.info(
          "skipped %s: %d bytes is over the fixture budget", doi, len(content)
        )
        continue
      target = papers_dir / f"{key}-{doi.replace('/', '_')}.pdf"
      target.write_bytes(content)
      entries.append(bibtex_entry(work, key))
      provenance.append(
        {
          "file": target.name,
          "doi": doi,
          "title": (work.get("title") or "").rstrip("."),
          "journal": ((work.get("primary_location") or {}).get("source") or {}).get(
            "display_name", ""
          ),
          "year": work.get("publication_year"),
          "license": location.get("license"),
          "source_url": pdf_url,
          "openalex_id": work.get("id"),
          "bytes": len(content),
        }
      )
      logger.info("kept %s (%d bytes)", doi, len(content))

  (CORPUS_DIR / "corpus.bib").write_text("\n".join(entries), encoding="utf-8")
  (CORPUS_DIR / "provenance.json").write_text(
    json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
  )
  logger.info("wrote %d papers to %s", len(provenance), CORPUS_DIR)
  return 0 if len(provenance) >= 5 else 1


if __name__ == "__main__":
  sys.exit(main())
