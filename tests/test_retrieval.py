"""Behavior of Retrieval Gap detection: a manifest in, out-of-corpus candidates out."""

import json
from pathlib import Path

from research_gap_dashboard import cli
from research_gap_dashboard.cli import EXIT_ERROR, EXIT_OK, main
from research_gap_dashboard.ingest import CorpusManifest, Paper
from research_gap_dashboard.retrieval import (
  OUT_OF_CORPUS_LABEL,
  RETRIEVAL_GAPS_NAME,
  RetrievalCandidate,
  detect_retrieval_gaps,
  read_retrieval_gaps,
)
from research_gap_dashboard.sources import OpenAlexAdapter


def _openalex(works: dict[str, dict]) -> OpenAlexAdapter:
  """Build a real OpenAlex adapter over a fake fetch keyed by cited-work id."""

  def fetch(url: str) -> dict | None:
    work_key = url.split("/works/", 1)[1].split("?", 1)[0]
    return works.get(work_key)

  return OpenAlexAdapter(fetch=fetch)


def _paper(key: str, openalex_id: str, refs: list[str], doi: str = "") -> Paper:
  """Build one Corpus Paper with a citation list, as ingest --resolve records it."""
  return Paper(
    citation_key=key,
    doi=doi or f"10.9999/{key}",
    title=key.title(),
    pdf_path=Path(f"papers/{key}.pdf"),
    openalex_id=openalex_id,
    referenced_works=refs,
  )


def _write_manifest(root: Path, papers: list[Paper]) -> None:
  """Write a CorpusManifest artifact into a corpus directory."""
  manifest = CorpusManifest(
    corpus_root=root, papers=papers, unmatched_entries=[], orphan_pdfs=[]
  )
  artifacts = root / "artifacts"
  artifacts.mkdir(parents=True, exist_ok=True)
  (artifacts / "corpus-manifest.json").write_text(
    manifest.model_dump_json(indent=2), encoding="utf-8"
  )


# Four Corpus Papers and the works they cite. W100 is cited by three Papers,
# W200 by two, W300 and W400 by one each; W3 is a Corpus Paper cited by another.
_PAPERS = [
  _paper("p01", "https://openalex.org/W1", ["W100", "W200", "W300"]),
  _paper("p02", "https://openalex.org/W2", ["W100", "W200"]),
  _paper("p03", "https://openalex.org/W3", ["W100", "https://openalex.org/W3"]),
  _paper("p04", "https://openalex.org/W4", ["W400"]),
]


def _work(work_id: str, doi: str, title: str) -> dict:
  """Shape a trimmed OpenAlex /works payload for one cited work."""
  return {
    "id": f"https://openalex.org/{work_id}",
    "doi": f"https://doi.org/{doi}",
    "title": title,
  }


_WORKS = {
  "W100": _work("W100", "10.1/aaa", "Shared A"),
  "W200": _work("W200", "10.2/bbb", "Shared B"),
  "W300": _work("W300", "10.3/ccc", "Cited once"),
  "W400": _work("W400", "10.4/ddd", "Cited once too"),
}


def test_candidates_are_ranked_by_citation_overlap(tmp_path: Path):
  """The most-shared cited works come first, each with the Papers that cite it."""
  _write_manifest(tmp_path, _PAPERS)

  report = detect_retrieval_gaps(tmp_path, _openalex(_WORKS))

  assert [c.title for c in report.candidates] == ["Shared A", "Shared B"]
  first = report.candidates[0]
  assert first.citation_overlap == 3
  assert first.citing_citation_keys == ["p01", "p02", "p03"]
  assert report.candidates[1].citation_overlap == 2
  assert report.candidates[1].citing_citation_keys == ["p01", "p02"]


def test_works_below_the_overlap_threshold_are_dropped(tmp_path: Path):
  """A work only one Corpus Paper cites is not a shared-citation signal."""
  _write_manifest(tmp_path, _PAPERS)

  report = detect_retrieval_gaps(tmp_path, _openalex(_WORKS), min_overlap=2)

  titles = {c.title for c in report.candidates}
  assert "Cited once" not in titles
  assert "Cited once too" not in titles


def test_a_single_paper_can_share_a_citation_when_overlap_is_one(tmp_path: Path):
  """Lowering the threshold surfaces once-cited works, still ranked by overlap."""
  _write_manifest(tmp_path, _PAPERS)

  report = detect_retrieval_gaps(tmp_path, _openalex(_WORKS), min_overlap=1)

  assert [c.citation_overlap for c in report.candidates] == [3, 2, 1, 1]


def test_works_already_in_the_corpus_never_appear_by_id(tmp_path: Path):
  """A cited work that is itself a Corpus Paper is excluded up front."""
  _write_manifest(tmp_path, _PAPERS)

  report = detect_retrieval_gaps(tmp_path, _openalex(_WORKS), min_overlap=1)

  assert all(c.openalex_id != "https://openalex.org/W3" for c in report.candidates)


def test_works_already_in_the_corpus_never_appear_by_doi(tmp_path: Path):
  """A candidate whose resolved DOI is already in the Corpus is dropped."""
  papers = [*_PAPERS, _paper("p05", "https://openalex.org/W5", [], doi="10.2/bbb")]
  _write_manifest(tmp_path, papers)

  report = detect_retrieval_gaps(tmp_path, _openalex(_WORKS))

  assert [c.title for c in report.candidates] == ["Shared A"]


def test_candidates_are_capped_at_the_limit(tmp_path: Path):
  """No more than `limit` candidates are written, keeping the highest overlap."""
  _write_manifest(tmp_path, _PAPERS)

  report = detect_retrieval_gaps(tmp_path, _openalex(_WORKS), limit=1, min_overlap=1)

  assert len(report.candidates) == 1
  assert report.candidates[0].title == "Shared A"


def test_unresolvable_cited_works_are_skipped(tmp_path: Path):
  """A cited work OpenAlex cannot resolve is skipped, not fatal."""
  _write_manifest(tmp_path, _PAPERS)

  works = {k: v for k, v in _WORKS.items() if k != "W100"}
  report = detect_retrieval_gaps(tmp_path, _openalex(works))

  assert [c.title for c in report.candidates] == ["Shared B"]


def test_report_is_labelled_out_of_corpus_and_written(tmp_path: Path):
  """The artifact records the denominator, source, and the out-of-corpus banner."""
  _write_manifest(tmp_path, _PAPERS)

  report = detect_retrieval_gaps(tmp_path, _openalex(_WORKS))

  assert report.label == OUT_OF_CORPUS_LABEL
  assert report.source == "openalex"
  assert report.corpus_paper_count == 4
  assert report.coupled_paper_count == 4

  written = json.loads(
    (tmp_path / "artifacts" / RETRIEVAL_GAPS_NAME).read_text(encoding="utf-8")
  )
  assert written["label"] == OUT_OF_CORPUS_LABEL
  assert read_retrieval_gaps(tmp_path).candidates[0].title == "Shared A"


def test_no_candidates_carry_in_corpus_evidence(tmp_path: Path):
  """Out-of-corpus candidates expose no Evidence field to link into the Corpus."""
  _write_manifest(tmp_path, _PAPERS)

  detect_retrieval_gaps(tmp_path, _openalex(_WORKS))

  assert "evidence" not in RetrievalCandidate.model_json_schema()["properties"]


def test_retrieve_command_writes_the_artifact(tmp_path: Path, monkeypatch):
  """The `retrieve` CLI command runs detection over the manifest and exits OK."""
  _write_manifest(tmp_path, _PAPERS)
  monkeypatch.setattr(cli, "OpenAlexAdapter", lambda **_: _openalex(_WORKS))

  exit_code = main(["retrieve", str(tmp_path)])

  assert exit_code == EXIT_OK
  assert (tmp_path / "artifacts" / RETRIEVAL_GAPS_NAME).is_file()


def test_retrieve_command_without_a_manifest_errors(tmp_path: Path, monkeypatch):
  """Retrieval needs a manifest; without one the command fails clearly."""
  (tmp_path / "artifacts").mkdir()
  monkeypatch.setattr(cli, "OpenAlexAdapter", lambda **_: _openalex(_WORKS))

  assert main(["retrieve", str(tmp_path)]) == EXIT_ERROR
