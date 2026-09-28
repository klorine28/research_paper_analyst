"""Behavior of the source-adapter seam and its OpenAlex and PubMed adapters."""

from research_gap_dashboard.sources import OpenAlexAdapter, PubMedAdapter

# A trimmed OpenAlex /works response, shaped like the real API.
WORK_RESPONSE = {
  "id": "https://openalex.org/W2741809807",
  "doi": "https://doi.org/10.7717/peerj.4375",
  "title": "The state of OA",
  "publication_year": 2018,
  "cited_by_count": 1169,
  "authorships": [
    {"author": {"display_name": "Heather Piwowar"}},
    {"author": {"display_name": "Jason Priem"}},
  ],
  "primary_location": {"source": {"display_name": "PeerJ"}},
  "referenced_works": [
    "https://openalex.org/W111",
    "https://openalex.org/W222",
  ],
}


def _fetch_returning(payload, recorder=None):
  """Build a fake JSON fetch that records its URL and returns `payload`."""

  def fetch(url):
    if recorder is not None:
      recorder.append(url)
    return payload

  return fetch


def test_resolve_returns_canonical_metadata():
  """A resolved DOI yields the canonical title, year, venue, and authors."""
  adapter = OpenAlexAdapter(fetch=_fetch_returning(WORK_RESPONSE))

  record = adapter.resolve("10.7717/peerj.4375")

  assert record is not None
  assert record.doi == "10.7717/peerj.4375"
  assert record.title == "The state of OA"
  assert record.year == 2018
  assert record.venue == "PeerJ"
  assert record.authors == ["Heather Piwowar", "Jason Priem"]
  assert record.cited_by_count == 1169
  assert record.referenced_works == [
    "https://openalex.org/W111",
    "https://openalex.org/W222",
  ]
  assert record.openalex_id == "https://openalex.org/W2741809807"


def test_resolve_queries_the_doi_endpoint():
  """The adapter asks OpenAlex for the work by DOI."""
  urls: list[str] = []
  adapter = OpenAlexAdapter(fetch=_fetch_returning(WORK_RESPONSE, urls))

  adapter.resolve("10.7717/peerj.4375")

  assert len(urls) == 1
  assert urls[0].startswith("https://api.openalex.org/works/doi:10.7717/peerj.4375")


def test_resolve_returns_none_when_the_work_is_absent():
  """A DOI OpenAlex does not know about resolves to nothing, not an error."""
  adapter = OpenAlexAdapter(fetch=_fetch_returning(None))

  assert adapter.resolve("10.0000/missing") is None


def test_adapter_names_its_source():
  """Adapters carry a stable name so the manifest can record provenance."""
  assert OpenAlexAdapter(fetch=_fetch_returning(None)).name == "openalex"


def test_fetch_work_resolves_a_bare_openalex_id():
  """A referenced work id resolves to a record, its DOI read off the payload."""
  urls: list[str] = []
  adapter = OpenAlexAdapter(fetch=_fetch_returning(WORK_RESPONSE, urls))

  record = adapter.fetch_work("https://openalex.org/W2741809807")

  assert record is not None
  assert record.doi == "10.7717/peerj.4375"
  assert record.openalex_id == "https://openalex.org/W2741809807"
  assert record.title == "The state of OA"
  assert urls[0].startswith("https://api.openalex.org/works/W2741809807")


def test_fetch_work_returns_none_when_absent():
  """A cited work OpenAlex no longer serves resolves to nothing, not an error."""
  adapter = OpenAlexAdapter(fetch=_fetch_returning(None))

  assert adapter.fetch_work("https://openalex.org/W999") is None


# Canned NCBI E-utilities responses, shaped like the real JSON API.
ESEARCH_RESPONSE = {"esearchresult": {"idlist": ["29456894"]}}
ESUMMARY_RESPONSE = {
  "result": {
    "29456894": {
      "uid": "29456894",
      "title": "The state of OA.",
      "pubdate": "2018 Feb 13",
      "fulljournalname": "PeerJ",
      "authors": [
        {"name": "Piwowar H", "authtype": "Author"},
        {"name": "Priem J", "authtype": "Author"},
        {"name": "Some Collaboration", "authtype": "CollectiveName"},
      ],
    }
  }
}


def _pubmed_fetch(recorder=None):
  """Build a fake fetch that answers esearch and esummary URLs in turn."""

  def fetch(url):
    if recorder is not None:
      recorder.append(url)
    if "esearch" in url:
      return ESEARCH_RESPONSE
    if "esummary" in url:
      return ESUMMARY_RESPONSE
    return None

  return fetch


def test_pubmed_resolve_returns_canonical_metadata():
  """PubMed resolution yields title, year, venue, and human authors."""
  adapter = PubMedAdapter(fetch=_pubmed_fetch())

  record = adapter.resolve("10.7717/peerj.4375")

  assert record is not None
  assert record.doi == "10.7717/peerj.4375"
  assert record.title == "The state of OA"
  assert record.year == 2018
  assert record.venue == "PeerJ"
  assert record.authors == ["Piwowar H", "Priem J"]


def test_pubmed_resolve_queries_esearch_then_esummary():
  """The adapter searches PubMed by DOI, then summarizes the PMID it finds."""
  urls: list[str] = []
  adapter = PubMedAdapter(fetch=_pubmed_fetch(urls))

  adapter.resolve("10.7717/peerj.4375")

  assert len(urls) == 2
  assert "esearch.fcgi" in urls[0]
  assert "10.7717%2Fpeerj.4375%5BAID%5D" in urls[0]
  assert "esummary.fcgi" in urls[1]
  assert "id=29456894" in urls[1]


def test_pubmed_resolve_returns_none_when_no_pmid_matches():
  """A DOI PubMed does not index resolves to nothing, not an error."""
  adapter = PubMedAdapter(fetch=_fetch_returning({"esearchresult": {"idlist": []}}))

  assert adapter.resolve("10.0000/missing") is None


def test_pubmed_adapter_names_its_source():
  """The second adapter carries its own stable source name."""
  assert PubMedAdapter(fetch=_pubmed_fetch()).name == "pubmed"
