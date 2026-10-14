"""Behavior of the source-adapter seam and its OpenAlex and PubMed adapters."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from research_gap_dashboard.sources import (
  OpenAlexAdapter,
  PubMedAdapter,
  SourceIdentity,
  TopicSearchSource,
)

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


# --- Topic search (ADR 0009 §3), against responses recorded from the live APIs.

RESPONSES = Path(__file__).parent / "fixtures" / "source-responses"
OPENALEX_SEARCH = json.loads((RESPONSES / "openalex_search.json").read_text())
PUBMED_ESEARCH = json.loads((RESPONSES / "pubmed_esearch.json").read_text())
PUBMED_EFETCH = (RESPONSES / "pubmed_efetch.xml").read_text()


def test_openalex_search_returns_work_records_with_ids_and_abstracts():
  """A topic query yields records carrying ids, title, year, and abstract."""
  adapter = OpenAlexAdapter(fetch=_fetch_returning(OPENALEX_SEARCH))

  records = adapter.search("atrial fibrillation anticoagulation elderly")

  assert [record.openalex_id for record in records] == [
    "https://openalex.org/W2138097413",
    "https://openalex.org/W2790204922",
    "https://openalex.org/W2062088294",
  ]
  second = records[1]
  assert second.doi == "10.1161/circulationaha.117.031658"
  assert second.pmid == "29490992"
  assert second.title.startswith("Oral Anticoagulation in Very Elderly Patients")
  assert second.year == 2018
  assert second.abstract.startswith(
    _first_words_of_inverted_index(OPENALEX_SEARCH["results"][1])
  )
  assert all(record.abstract for record in records)


def _first_words_of_inverted_index(work, count=4):
  """Rebuild the first words of an OpenAlex abstract, independently of the code."""
  positions = {
    position: word
    for word, indices in work["abstract_inverted_index"].items()
    for position in indices
  }
  return " ".join(positions[index] for index in range(count))


def test_openalex_search_queries_the_works_endpoint_politely():
  """The search URL carries the query, the page size, and the polite-pool mailto."""
  urls: list[str] = []
  adapter = OpenAlexAdapter(
    fetch=_fetch_returning(OPENALEX_SEARCH, urls), mailto="me+gaps@example.org"
  )

  adapter.search("atrial fibrillation", limit=3)

  assert len(urls) == 1
  url = urlparse(urls[0])
  params = parse_qs(url.query)
  assert url.path == "/works"
  assert params["search"] == ["atrial fibrillation"]
  assert params["per-page"] == ["3"]
  assert params["mailto"] == ["me+gaps@example.org"]
  assert "abstract_inverted_index" in params["select"][0]


def test_openalex_search_returns_nothing_for_an_empty_response():
  """No results (or no response at all) is an empty list, not an error."""
  assert not OpenAlexAdapter(fetch=_fetch_returning({"results": []})).search("x")
  assert not OpenAlexAdapter(fetch=_fetch_returning(None)).search("x")


def _pubmed_search_fetches(recorder=None, efetch_body=PUBMED_EFETCH):
  """Build fake JSON and text fetches answering esearch and efetch URLs."""

  def fetch(url):
    if recorder is not None:
      recorder.append(url)
    return PUBMED_ESEARCH if "esearch" in url else None

  def fetch_text(url):
    if recorder is not None:
      recorder.append(url)
    return efetch_body if "efetch" in url else None

  return fetch, fetch_text


def test_pubmed_search_returns_work_records_with_ids_and_abstracts():
  """A topic query yields records carrying PMID, DOI, title, year, and abstract."""
  fetch, fetch_text = _pubmed_search_fetches()
  adapter = PubMedAdapter(fetch=fetch, fetch_text=fetch_text)

  records = adapter.search("atrial fibrillation anticoagulation elderly")

  assert [record.pmid for record in records] == ["35167115", "32936558", "37758192"]
  first = records[0]
  assert first.doi == "10.33963/kp.a2022.0046"
  assert first.title == (
    "Optimal anticoagulation in elderly patients with atrial fibrillation: "
    "Which drug at which dose?"
  )
  assert first.year == 2022
  assert first.venue == "Kardiologia polska"
  assert first.abstract.startswith("Aging is an important risk factor")
  assert records[2].doi == "10.1146/annurev-pharmtox-032823-122811"
  assert all(record.abstract for record in records)


def test_pubmed_search_keeps_a_record_without_a_doi():
  """A PubMed record with no DOI still comes back, identified by its PMID."""
  fetch, fetch_text = _pubmed_search_fetches()
  adapter = PubMedAdapter(fetch=fetch, fetch_text=fetch_text)

  second = adapter.search("atrial fibrillation")[1]

  assert second.pmid == "32936558"
  assert not second.doi
  assert second.year == 2020


def test_pubmed_search_queries_esearch_then_efetch_with_the_api_key():
  """The adapter searches by relevance, then fetches the PMIDs, keyed both times."""
  urls: list[str] = []
  fetch, fetch_text = _pubmed_search_fetches(urls)
  adapter = PubMedAdapter(fetch=fetch, fetch_text=fetch_text, api_key="KEY123")

  adapter.search("atrial fibrillation", limit=3)

  assert len(urls) == 2
  esearch, efetch = (parse_qs(urlparse(url).query) for url in urls)
  assert "esearch.fcgi" in urls[0]
  assert esearch["term"] == ["atrial fibrillation"]
  assert esearch["retmax"] == ["3"]
  assert esearch["sort"] == ["relevance"]
  assert esearch["api_key"] == ["KEY123"]
  assert "efetch.fcgi" in urls[1]
  assert efetch["id"] == ["35167115,32936558,37758192"]
  assert efetch["api_key"] == ["KEY123"]


def test_pubmed_search_skips_efetch_when_nothing_matches():
  """No PMIDs means no records and no second request."""
  urls: list[str] = []

  def fetch(url):
    urls.append(url)
    return {"esearchresult": {"idlist": []}}

  adapter = PubMedAdapter(fetch=fetch, fetch_text=lambda url: urls.append(url))

  assert not adapter.search("nothing matches this")
  assert len(urls) == 1


def test_both_adapters_satisfy_the_topic_search_seam():
  """Saturation checks can treat either source as a TopicSearchSource."""
  sources: list[TopicSearchSource] = [
    OpenAlexAdapter(fetch=_fetch_returning({"results": []})),
    PubMedAdapter(fetch=_fetch_returning({"esearchresult": {"idlist": []}})),
  ]

  assert [source.name for source in sources] == ["openalex", "pubmed"]
  assert all(source.search("x") == [] for source in sources)


def test_source_identity_reads_the_client_identifiers_from_the_environment():
  """The OpenAlex mailto and PubMed API key come from the environment, if set."""
  identity = SourceIdentity.from_env(
    {"OPENALEX_MAILTO": "me@example.org", "NCBI_API_KEY": "KEY123"}
  )

  assert identity.openalex_mailto == "me@example.org"
  assert identity.pubmed_api_key == "KEY123"


def test_source_identity_treats_blank_settings_as_unset():
  """Blank lines copied from .env.example identify nobody rather than ''."""
  identity = SourceIdentity.from_env({"OPENALEX_MAILTO": "", "NCBI_API_KEY": ""})

  assert identity.openalex_mailto is None
  assert identity.pubmed_api_key is None
