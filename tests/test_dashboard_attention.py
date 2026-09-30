"""
Behavior of the needs-attention read model and incompleteness banner (#46 Step 3).

The builder reads the manifest, the parse-stage summary, and the Extractions
artifact off disk and turns their recorded fallout into a per-Paper queue plus a
banner. It imports no pipeline stage (ADR 0002); these tests write the artifacts
directly and assert what the builder makes of them.
"""

import json
from pathlib import Path

from research_gap_dashboard.dashboard.attention import build_attention
from research_gap_dashboard.dashboard.grounding import PARSED_SUFFIX
from research_gap_dashboard.parsing import ParsedPaper, ParsedSection

_TEXT = "We studied outcomes in older patients across a decade of registry data."


def _write_json(path: Path, payload: dict) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(payload), encoding="utf-8")


def _write_manifest(root: Path, citation_keys: list[str]) -> None:
  _write_json(
    root / "artifacts" / "corpus-manifest.json",
    {
      "corpus_root": str(root),
      "papers": [
        {"citation_key": key, "doi": "10.0/x", "title": f"Title {key}"}
        for key in citation_keys
      ],
    },
  )


def _write_parsed(root: Path, citation_key: str) -> None:
  parsed = ParsedPaper(
    source_pdf=Path(f"{citation_key}.pdf"),
    sections=[ParsedSection(label="abstract", heading="Abstract", text=_TEXT)],
  )
  path = root / "paper-data" / f"{citation_key}{PARSED_SUFFIX}"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(parsed.model_dump_json(), encoding="utf-8")


def test_a_fully_complete_corpus_has_no_queue_and_a_green_banner(tmp_path: Path):
  """When every Paper parsed and extracted cleanly, the queue is empty."""
  _write_manifest(tmp_path, ["a", "b"])
  _write_parsed(tmp_path, "a")
  _write_parsed(tmp_path, "b")
  _write_json(
    tmp_path / "artifacts" / "extractions.json",
    {
      "corpus_root": str(tmp_path),
      "extractions": [{"citation_key": "a"}, {"citation_key": "b"}],
      "failures": [],
      "unverified": [],
    },
  )

  report = build_attention(tmp_path)

  assert report.items == []
  assert report.banner.is_complete
  assert report.banner.reached_artifact == 2


def test_the_queue_separates_unparsed_rejected_and_dropped(tmp_path: Path):
  """Each kind of fallout becomes its own queue item and banner count."""
  _write_manifest(tmp_path, ["scan", "reject", "partial", "ok"])
  # "scan" never parsed; the others did.
  for key in ("reject", "partial", "ok"):
    _write_parsed(tmp_path, key)
  _write_json(
    tmp_path / "artifacts" / "parse-report.json",
    {
      "corpus_root": str(tmp_path),
      "parsed_paths": [],
      "failures": [
        {"citation_key": "scan", "pdf_path": "scan.pdf", "error": "no usable text"}
      ],
    },
  )
  _write_json(
    tmp_path / "artifacts" / "extractions.json",
    {
      "corpus_root": str(tmp_path),
      "extractions": [{"citation_key": "partial"}, {"citation_key": "ok"}],
      "failures": [{"citation_key": "reject", "reason": "no fact verified"}],
      "unverified": [
        {"citation_key": "partial", "field": "methods", "passage": "x", "reason": "r"},
        {"citation_key": "partial", "field": "results", "passage": "y", "reason": "r"},
      ],
    },
  )

  report = build_attention(tmp_path)
  kinds = {item.citation_key: item.kind for item in report.items}

  assert kinds == {
    "scan": "unparsed",
    "reject": "extraction_failed",
    "partial": "facts_dropped",
  }
  assert "ok" not in kinds
  banner = report.banner
  assert not banner.is_complete
  assert banner.total_papers == 4
  assert banner.unparsed == 1
  assert banner.extraction_failed == 1
  assert banner.missing_papers == 2
  assert banner.reached_artifact == 2
  assert banner.papers_with_dropped_facts == 1
  assert banner.dropped_facts == 2
  scan = next(i for i in report.items if i.citation_key == "scan")
  assert scan.reason == "no usable text"
  assert scan.has_parsed_text is False


def test_no_manifest_yields_an_empty_report(tmp_path: Path):
  """Before ingest there is nothing to attend to, and no crash."""
  report = build_attention(tmp_path)

  assert report.items == []
  assert report.banner.total_papers == 0
  assert not report.banner.is_complete
