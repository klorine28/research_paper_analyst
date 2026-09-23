"""
The source-adapter seam: one protocol for every scholarly API the tool uses.

Ingest resolves each DOI to canonical metadata through an adapter; later,
Retrieval Gap detection reuses the same seam to reach out to the wider
literature. v1 ships the OpenAlex adapter here; a PubMed adapter can join it
by implementing the same protocol. Adapters own their HTTP; the rest of the
pipeline only sees `WorkRecord`s, so the suite runs offline by injecting a
fake `fetch`.
"""

import logging
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

OPENALEX_BASE_URL = "https://api.openalex.org"

# A JSON fetch: given a URL, return the parsed body, or None when there is no
# such work (an HTTP 404). Adapters depend on this, not on any HTTP library,
# so tests inject canned responses.
JsonFetch = Callable[[str], dict[str, Any] | None]


class WorkRecord(BaseModel):
  """Canonical metadata for one scholarly work, as a scholarly API reports it."""

  doi: str
  title: str = ""
  year: int | None = None
  venue: str = ""
  authors: list[str] = []
  openalex_id: str = ""
  referenced_works: list[str] = []
  cited_by_count: int = 0


class SourceAdapter(Protocol):  # pylint: disable=too-few-public-methods
  """A scholarly API the pipeline can resolve DOIs against."""

  name: str

  def resolve(self, doi: str) -> WorkRecord | None:
    """Return the canonical record for a DOI, or None if the source lacks it."""


class OpenAlexAdapter:  # pylint: disable=too-few-public-methods
  """Resolves DOIs against OpenAlex, the primary scholarly source for v1."""

  name = "openalex"

  # Only the fields ingest and Retrieval Gap detection need, to keep the
  # request cheap (single-entity lookups by DOI are free).
  _SELECT = (
    "id,doi,title,publication_year,authorships,"
    "primary_location,referenced_works,cited_by_count"
  )

  def __init__(self, fetch: JsonFetch | None = None, mailto: str | None = None):
    """Wrap OpenAlex; pass `mailto` to join the polite pool, or a fake `fetch`."""
    self._fetch = fetch or _httpx_fetch
    self._mailto = mailto

  def resolve(self, doi: str) -> WorkRecord | None:
    """Look the DOI up on OpenAlex and map the work onto a WorkRecord."""
    url = f"{OPENALEX_BASE_URL}/works/doi:{doi}?select={self._SELECT}"
    if self._mailto:
      url = f"{url}&mailto={self._mailto}"
    payload = self._fetch(url)
    if payload is None:
      return None
    return _work_record_from_openalex(doi, payload)


def _work_record_from_openalex(doi: str, work: dict[str, Any]) -> WorkRecord:
  """Map an OpenAlex work object onto a WorkRecord."""
  authors = [
    authorship["author"]["display_name"]
    for authorship in work.get("authorships", [])
    if authorship.get("author", {}).get("display_name")
  ]
  primary_location = work.get("primary_location") or {}
  source = primary_location.get("source") or {}
  return WorkRecord(
    doi=doi,
    title=work.get("title") or "",
    year=work.get("publication_year"),
    venue=source.get("display_name") or "",
    authors=authors,
    openalex_id=work.get("id") or "",
    referenced_works=list(work.get("referenced_works") or []),
    cited_by_count=work.get("cited_by_count") or 0,
  )


def _httpx_fetch(url: str) -> dict[str, Any] | None:
  """Fetch and parse JSON from a URL, treating 404 as 'no such work'."""
  response = httpx.get(url, follow_redirects=True, timeout=30.0)
  if response.status_code == httpx.codes.NOT_FOUND:
    return None
  response.raise_for_status()
  return response.json()
