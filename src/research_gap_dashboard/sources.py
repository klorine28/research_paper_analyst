"""
The source-adapter seam: one protocol for every scholarly API the tool uses.

Ingest resolves each DOI to canonical metadata through an adapter; later,
Retrieval Gap detection reuses the same seam to reach out to the wider
literature, and saturation checks (ADR 0009) search it by topic. v1 ships
two implementations: the OpenAlex adapter (the primary source, and the only
one that reports the citation graph Retrieval Gap detection ranks over) and
the PubMed adapter. Both satisfy the same protocols:
`SourceAdapter` (resolve a DOI) and `TopicSearchSource` (search by query).
Adapters own their HTTP; the rest of the pipeline only sees `WorkRecord`s, so
the suite runs offline by injecting a fake `fetch`.
"""

import logging
import os
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
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

# A text fetch, for the one endpoint that has no JSON mode: PubMed's efetch,
# the only E-utility that returns abstracts, answers in XML.
TextFetch = Callable[[str], str | None]

# Default page size for a topic search: enough to screen, cheap to fetch.
DEFAULT_SEARCH_LIMIT = 25


class WorkRecord(BaseModel):
  """Canonical metadata for one scholarly work, as a scholarly API reports it."""

  doi: str
  title: str = ""
  abstract: str = ""
  pmid: str = ""
  year: int | None = None
  venue: str = ""
  authors: list[str] = []
  openalex_id: str = ""
  referenced_works: list[str] = []
  cited_by_count: int = 0


class SourceIdentity(BaseModel):
  """
  How the tool identifies itself to scholarly APIs, read once from the env.

  Both are optional: without them the APIs still answer, but OpenAlex serves
  the slower common pool and PubMed allows 3 rather than 10 requests a second.
  """

  openalex_mailto: str | None = None
  pubmed_api_key: str | None = None

  @classmethod
  def from_env(cls, env: Mapping[str, str] | None = None) -> "SourceIdentity":
    """Read the client identifiers from `env` (defaulting to the process env)."""
    env = os.environ if env is None else env
    return cls(
      openalex_mailto=env.get("OPENALEX_MAILTO") or None,
      pubmed_api_key=env.get("NCBI_API_KEY") or None,
    )


class SourceAdapter(Protocol):  # pylint: disable=too-few-public-methods
  """A scholarly API the pipeline can resolve DOIs against."""

  name: str

  def resolve(self, doi: str) -> WorkRecord | None:
    """Return the canonical record for a DOI, or None if the source lacks it."""


class TopicSearchSource(Protocol):  # pylint: disable=too-few-public-methods
  """A scholarly API the pipeline can search by topic, outside the Corpus."""

  name: str

  def search(self, query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> list[WorkRecord]:
    """Return up to `limit` works matching `query`, most relevant first."""
    raise NotImplementedError


class OpenAlexAdapter:
  """Resolves DOIs against and searches OpenAlex, the primary scholarly source."""

  name = "openalex"

  # Only the fields ingest and Retrieval Gap detection need, to keep the
  # request cheap (single-entity lookups by DOI are free).
  _SELECT = (
    "id,doi,title,publication_year,authorships,"
    "primary_location,referenced_works,cited_by_count"
  )
  # A search hit is screened by its title and abstract, not ranked by its
  # references, so the (large) reference list is swapped for the abstract.
  _SEARCH_SELECT = (
    "id,doi,ids,title,publication_year,authorships,"
    "primary_location,cited_by_count,abstract_inverted_index"
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

  def search(self, query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> list[WorkRecord]:
    """Search OpenAlex works by relevance to `query` (title, abstract, full text)."""
    params = {"search": query, "per-page": str(limit), "select": self._SEARCH_SELECT}
    if self._mailto:
      params["mailto"] = self._mailto
    payload = self._fetch(f"{OPENALEX_BASE_URL}/works?{urlencode(params)}")
    if payload is None:
      return []
    return [_work_record_from_openalex(work) for work in payload.get("results") or []]

  def _work_url(self, work_ref: str) -> str:
    """Build a single-work URL for a DOI query or a bare OpenAlex work id."""
    url = f"{OPENALEX_BASE_URL}/works/{work_ref}?select={self._SELECT}"
    if self._mailto:
      url = f"{url}&{urlencode({'mailto': self._mailto})}"
    return url


class PubMedAdapter:
  """Resolves DOIs against and searches PubMed via NCBI E-utilities."""

  name = "pubmed"

  def __init__(
    self,
    fetch: JsonFetch | None = None,
    api_key: str | None = None,
    fetch_text: TextFetch | None = None,
  ):
    """Wrap PubMed; pass `api_key` to raise the rate limit, or fake fetches."""
    self._fetch = fetch or _httpx_fetch
    self._fetch_text = fetch_text or _httpx_fetch_text
    self._api_key = api_key

  def search(self, query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> list[WorkRecord]:
    """Search PubMed by relevance, then fetch the matching articles' abstracts."""
    payload = self._fetch(
      self._url("esearch.fcgi", term=query, retmax=str(limit), sort="relevance")
    )
    if payload is None:
      return []
    pmids = (payload.get("esearchresult") or {}).get("idlist") or []
    if not pmids:
      return []
    body = self._fetch_text(self._url("efetch.fcgi", id=",".join(pmids), retmode="xml"))
    if body is None:
      return []
    return _work_records_from_pubmed_xml(body)

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


def _work_records_from_pubmed_xml(body: str) -> list[WorkRecord]:
  """Map each PubmedArticle in an efetch XML response onto a WorkRecord."""
  # The stdlib parser never resolves external entities, and NCBI is the only
  # producer of this body, so defusedxml is not needed.
  root = ET.fromstring(body)  # noqa: S314
  return [_work_record_from_pubmed_article(art) for art in root.iter("PubmedArticle")]


def _work_record_from_pubmed_article(article: ET.Element) -> WorkRecord:
  """Map one PubmedArticle element onto a WorkRecord."""
  citation = article.find("MedlineCitation")
  if citation is None:
    citation = ET.Element("MedlineCitation")
  details = citation.find("Article")
  if details is None:
    details = ET.Element("Article")
  authors = [
    f"{author.findtext('LastName', '')} {author.findtext('Initials', '')}".strip()
    for author in details.iterfind("AuthorList/Author")
    if author.findtext("LastName")
  ]
  return WorkRecord(
    doi=_pubmed_doi(article, details),
    pmid=citation.findtext("PMID", "").strip(),
    title=_element_text(details.find("ArticleTitle")).rstrip("."),
    abstract=_pubmed_abstract(details),
    year=_pubmed_year(details),
    venue=details.findtext("Journal/Title", "").strip(),
    authors=authors,
  )


def _pubmed_doi(article: ET.Element, details: ET.Element) -> str:
  """Find an article's DOI in its id list, falling back to its location ids."""
  candidates = [
    *article.iterfind("PubmedData/ArticleIdList/ArticleId[@IdType='doi']"),
    *details.iterfind("ELocationID[@EIdType='doi']"),
  ]
  for candidate in candidates:
    doi = _element_text(candidate)
    if doi:
      return doi.lower()
  return ""


def _pubmed_abstract(details: ET.Element) -> str:
  """Join a (possibly structured) abstract, prefixing each part with its label."""
  parts = []
  for section in details.iterfind("Abstract/AbstractText"):
    text = _element_text(section)
    label = section.get("Label")
    if text:
      parts.append(f"{label}: {text}" if label else text)
  return "\n".join(parts)


def _pubmed_year(details: ET.Element) -> int | None:
  """Read the issue year, or the leading year of a free-text MedlineDate."""
  pubdate = details.find("Journal/JournalIssue/PubDate")
  if pubdate is None:
    return None
  return _leading_year(pubdate.findtext("Year") or pubdate.findtext("MedlineDate", ""))


def _element_text(element: ET.Element | None) -> str:
  """All text inside an element, inline markup (italics, sub/sup) flattened."""
  if element is None:
    return ""
  return " ".join("".join(element.itertext()).split())


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
    abstract=_abstract_from_inverted_index(work.get("abstract_inverted_index")),
    pmid=((work.get("ids") or {}).get("pmid") or "").rsplit("/", 1)[-1],
    year=work.get("publication_year"),
    venue=source.get("display_name") or "",
    authors=authors,
    openalex_id=work.get("id") or "",
    referenced_works=list(work.get("referenced_works") or []),
    cited_by_count=work.get("cited_by_count") or 0,
  )


def _abstract_from_inverted_index(index: dict[str, list[int]] | None) -> str:
  """Rebuild an abstract from OpenAlex's word -> positions inverted index."""
  if not index:
    return ""
  positioned = sorted(
    (position, word) for word, positions in index.items() for position in positions
  )
  return " ".join(word for _, word in positioned)


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


def _httpx_fetch_text(url: str) -> str | None:
  """Fetch a URL's body as text, treating 404 as 'no such resource'."""
  response = httpx.get(url, follow_redirects=True, timeout=30.0)
  if response.status_code == httpx.codes.NOT_FOUND:
    return None
  response.raise_for_status()
  return response.text
