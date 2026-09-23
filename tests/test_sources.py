"""Behavior of the source-adapter seam and its OpenAlex implementation."""

from research_gap_dashboard.sources import OpenAlexAdapter

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
