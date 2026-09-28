"""
The source-adapter seam: one protocol for every scholarly API the tool uses.

Ingest resolves each DOI to canonical metadata through an adapter; later,
Retrieval Gap detection reuses the same seam to reach out to the wider
literature. v1 ships two implementations: the OpenAlex adapter (the primary
source, and the only one that reports the citation graph Retrieval Gap
detection ranks over) and the PubMed adapter. Both satisfy the same protocol.
Adapters own their HTTP; the rest of the pipeline only sees `WorkRecord`s, so
the suite runs offline by injecting a fake `fetch`.
"""

import logging
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

OPENALEX_BASE_URL = "https://api.openalex.org"
PUBMED_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

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
    payload = self._fetch(self._work_url(f"doi:{doi}"))
    if payload is None:
      return None
    return _work_record_from_openalex(payload, doi=doi)

  def fetch_work(self, openalex_id: str) -> WorkRecord | None:
    """
    Fetch one work by its OpenAlex id (e.g. a Corpus Paper's referenced work).

    Retrieval Gap detection ranks Corpus Papers' referenced works by citation
    overlap and then resolves the survivors here, so a candidate carries a
    title and DOI rather than a bare id. Its DOI comes from the payload, not a
    known query, so it can be checked against the Corpus.
    """
    work_key = openalex_id.rsplit("/", 1)[-1]
    payload = self._fetch(self._work_url(work_key))
    if payload is None:
      return None
    return _work_record_from_openalex(payload)

  def _work_url(self, work_ref: str) -> str:
    """Build a single-work URL for a DOI query or a bare OpenAlex work id."""
    url = f"{OPENALEX_BASE_URL}/works/{work_ref}?select={self._SELECT}"
    if self._mailto:
      url = f"{url}&mailto={self._mailto}"
    return url


class PubMedAdapter:  # pylint: disable=too-few-public-methods
  """Resolves DOIs against PubMed via NCBI E-utilities, the second v1 source."""

  name = "pubmed"

  def __init__(self, fetch: JsonFetch | None = None, api_key: str | None = None):
    """Wrap PubMed; pass `api_key` to raise the rate limit, or a fake `fetch`."""
    self._fetch = fetch or _httpx_fetch
    self._api_key = api_key

  def resolve(self, doi: str) -> WorkRecord | None:
    """Find the PubMed record for a DOI and map its summary onto a WorkRecord."""
    pmid = self._find_pmid(doi)
    if pmid is None:
      return None
    return self._summarize(doi, pmid)

  def _find_pmid(self, doi: str) -> str | None:
    """Search PubMed for the PMID carrying this DOI as its article id."""
    payload = self._fetch(self._url("esearch.fcgi", term=f"{doi}[AID]"))
    if payload is None:
      return None
    idlist = (payload.get("esearchresult") or {}).get("idlist") or []
    return idlist[0] if idlist else None

  def _summarize(self, doi: str, pmid: str) -> WorkRecord | None:
    """Fetch a PMID's document summary and map it onto a WorkRecord."""
    payload = self._fetch(self._url("esummary.fcgi", id=pmid))
    if payload is None:
      return None
    doc = (payload.get("result") or {}).get(pmid)
    if not doc:
      return None
    authors = [
      author["name"]
      for author in doc.get("authors", [])
      if author.get("name") and author.get("authtype", "Author") == "Author"
    ]
    return WorkRecord(
      doi=doi,
      title=(doc.get("title") or "").rstrip("."),
      year=_leading_year(doc.get("pubdate", "")),
      venue=doc.get("fulljournalname") or "",
      authors=authors,
    )

  def _url(self, endpoint: str, **params: str) -> str:
    """Build an E-utilities URL, always in JSON mode and with the API key."""
    query = {"db": "pubmed", "retmode": "json", **params}
    if self._api_key:
      query["api_key"] = self._api_key
    return f"{PUBMED_BASE_URL}/{endpoint}?{urlencode(query)}"


def _leading_year(pubdate: str) -> int | None:
  """Pull the four-digit year off a PubMed pubdate like '2018 Feb 12'."""
  head = pubdate.strip()[:4]
  return int(head) if head.isdigit() else None


def _work_record_from_openalex(
  work: dict[str, Any], doi: str | None = None
) -> WorkRecord:
  """Map an OpenAlex work object onto a WorkRecord, deriving its DOI if unknown."""
  authors = [
    authorship["author"]["display_name"]
    for authorship in work.get("authorships", [])
    if authorship.get("author", {}).get("display_name")
  ]
  primary_location = work.get("primary_location") or {}
  source = primary_location.get("source") or {}
  return WorkRecord(
    doi=doi if doi is not None else _doi_from_work(work),
    title=work.get("title") or "",
    year=work.get("publication_year"),
    venue=source.get("display_name") or "",
    authors=authors,
    openalex_id=work.get("id") or "",
    referenced_works=list(work.get("referenced_works") or []),
    cited_by_count=work.get("cited_by_count") or 0,
  )


def _doi_from_work(work: dict[str, Any]) -> str:
  """Reduce an OpenAlex work's DOI URL to a bare, lower-cased DOI."""
  doi_url = work.get("doi") or ""
  return doi_url.removeprefix("https://doi.org/").lower()


def _httpx_fetch(url: str) -> dict[str, Any] | None:
  """Fetch and parse JSON from a URL, treating 404 as 'no such work'."""
  response = httpx.get(url, follow_redirects=True, timeout=30.0)
  if response.status_code == httpx.codes.NOT_FOUND:
    return None
  response.raise_for_status()
  return response.json()
