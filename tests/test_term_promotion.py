"""Behavior of offering recurring unmapped terms for promotion to aliases."""

import json
from pathlib import Path

import pytest

from research_gap_dashboard.aggregate import (
  NORMALIZED_FACTS_NAME,
  AggregateReport,
  NormalizedFacts,
  UnmappedTerm,
)
from research_gap_dashboard.cli import main
from research_gap_dashboard.extract import (
  EXTRACTIONS_NAME,
  Evidence,
  ExtractedFact,
  Extraction,
  ExtractionFields,
  ExtractReport,
)
from research_gap_dashboard.taxonomy import Axis, Taxonomy, load_taxonomy
from research_gap_dashboard.term_promotion import (
  NoiseClass,
  noise_class,
  promote_unmapped_term,
  screen_unmapped_terms,
)
from research_gap_dashboard.vocabulary import (
  AliasOverlay,
  AliasOverlayError,
  apply_alias_overlay,
  promote_alias,
  reject_alias,
)

TAXONOMY = """
[meta]
domain = "cardiology"
source = "MeSH 2025, trimmed for these tests"

[[topics]]
id = "nervous-system-diseases"
label = "Nervous System Diseases"
mesh_id = "D009422"
aliases = []

[[topics]]
id = "takotsubo-cardiomyopathy"
label = "Takotsubo Cardiomyopathy"
mesh_id = "D054549"
aliases = ["Takotsubo Syndrome"]
"""

# Noise a model leaves unmapped, one or more per class the stoplist rejects.
NOISE: list[tuple[str, NoiseClass]] = [
  ("2019", "number"),
  ("12.5", "number"),
  ("18-65", "number"),
  ("three", "number"),
  ("mg/kg", "unit"),
  ("mmHg", "unit"),
  ("120 mmHg", "unit"),
  ("30 days", "unit"),
  ("5 mg", "dosage"),
  ("aspirin 100 mg daily", "dosage"),
  ("0.5 mg/kg", "dosage"),
  ("p < 0.05", "statistic"),
  ("95% CI", "statistic"),
  ("OR = 2.3", "statistic"),
  ("n = 120", "statistic"),
  ("42%", "statistic"),
  ("mean ± SD", "statistic"),
  ("hazard ratio", "statistic"),
  ("Smith et al.", "citation"),
  ("[12]", "citation"),
  ("Fig. 2", "citation"),
  ("Table 3", "citation"),
  ("doi:10.1000/xyz", "citation"),
  ("J. Smith", "author_name"),
  ("Smith, J.", "author_name"),
  ("Smith and Jones (2020)", "author_name"),
  ("Smith 2019", "author_name"),
  ("patients", "generic"),
  ("study", "generic"),
  ("results", "generic"),
  ("the study patients", "generic"),
  ("clinical outcomes", "generic"),
  ("of the", "stopword_heavy"),
  ("in the setting of", "stopword_heavy"),
  ("use of the", "stopword_heavy"),
  (
    "patients who presented to the emergency department with chest pain and dyspnea",
    "too_long",
  ),
]

REAL_TERMS = [
  "neurologic disorders",
  "Takotsubo syndrome",
  "type 2 diabetes",
  "COVID-19",
  "quality of life",
  "Brugada syndrome",
  "older adults",
  "ST-segment elevation",
  "left ventricular ejection fraction",
  "BMI",
  "Nationwide Inpatient Sample",
  "Functional MRI",
  "Hepatitis B",
  "Factor XII deficiency",
]


@pytest.mark.parametrize(("term", "expected"), NOISE)
def test_stoplist_rejects_each_noise_class(term: str, expected: NoiseClass):
  """Numbers, units, dosages, statistics, citations, names and filler are noise."""
  assert noise_class(term) == expected


@pytest.mark.parametrize("term", REAL_TERMS)
def test_stoplist_keeps_real_terms(term: str):
  """Domain terms pass, including ones that hold a digit or an eponym."""
  assert noise_class(term) is None


def fact(passage: str) -> ExtractedFact:
  """Build a verified fact quoting this passage."""
  return ExtractedFact(
    statement=passage, evidence=Evidence(passage=passage, section="results")
  )


def paper(key: str, passages: list[str]) -> Extraction:
  """Build a Paper whose key findings quote these passages."""
  return Extraction(
    citation_key=key,
    fields=ExtractionFields(key_findings=[fact(p) for p in passages]),
  )


def normalized(key: str, terms: list[str], axis: Axis = "topic") -> NormalizedFacts:
  """Build a Paper's NormalizedFacts with these phrases left unmapped."""
  return NormalizedFacts(
    citation_key=key,
    assignments=[],
    unmapped=[
      UnmappedTerm(axis=axis, original_term=t, reason="none fits") for t in terms
    ],
  )


def reports() -> tuple[AggregateReport, ExtractReport]:
  """Build a three-Paper Corpus mixing recurring, single, unverified and noise terms."""
  extractions = [
    paper(
      "alpha2020",
      [
        "Neurologic disorders were frequent (p < 0.05) among cases.",
        "Pheochromocytoma was found once.",
        "Patients received 5 mg of the drug.",
      ],
    ),
    paper(
      "beta2021",
      [
        "History of neurologic disorders was recorded.",
        "Patients received 5 mg daily.",
      ],
    ),
    paper("gamma2022", ["Chest pain was the presenting symptom."]),
  ]
  normalized_facts = [
    normalized("alpha2020", ["Neurologic disorders", "pheochromocytoma", "5 mg"]),
    normalized(
      "beta2021",
      ["neurologic  disorders", "5 mg", "chest pain", "Takotsubo syndrome"],
    ),
    # "anxiety" is not in either Paper's verified Evidence: a model invention.
    normalized("gamma2022", ["chest pain", "anxiety"]),
  ]
  normalized_facts[0].unmapped.append(
    UnmappedTerm(axis="topic", original_term="anxiety", reason="none fits")
  )
  aggregate = AggregateReport(
    corpus_root=Path("corpus"),
    prompt_version="aggregate-test",
    tier="default",
    taxonomies=[],
    normalized=normalized_facts,
    failures=[],
  )
  extract = ExtractReport(
    corpus_root=Path("corpus"),
    prompt_version="extract-test",
    tier="default",
    extractions=extractions,
    failures=[],
  )
  return aggregate, extract


@pytest.fixture(name="taxonomy_path")
def fixture_taxonomy_path(tmp_path: Path) -> Path:
  """Write the shared Topic taxonomy and return its path."""
  path = tmp_path / "topics.toml"
  path.write_text(TAXONOMY, encoding="utf-8")
  return path


@pytest.fixture(name="taxonomy")
def fixture_taxonomy(taxonomy_path: Path) -> Taxonomy:
  """Load the shared Topic taxonomy."""
  return load_taxonomy(taxonomy_path)


def test_only_verified_recurring_real_terms_are_offered(taxonomy: Taxonomy):
  """A term is offered when verified Evidence in 2+ Papers holds it, and not noise."""
  aggregate, extract = reports()

  screen = screen_unmapped_terms(aggregate, extract, [taxonomy], AliasOverlay())

  assert [(c.axis, c.term, c.citation_keys) for c in screen.offered] == [
    ("topic", "Neurologic disorders", ["alpha2020", "beta2021"])
  ]
  offered = screen.offered[0]
  assert [e.passage for e in offered.evidence] == [
    "Neurologic disorders were frequent (p < 0.05) among cases.",
    "History of neurologic disorders was recorded.",
  ]
  reasons = {term.term.casefold(): term.reason for term in screen.set_aside}
  assert reasons == {
    "pheochromocytoma": "single_paper",
    # Unmapped in two Papers, but only one Paper's Evidence holds it.
    "chest pain": "single_paper",
    "anxiety": "unverified",
    "5 mg": "dosage",
    "takotsubo syndrome": "already_mapped",
  }


def test_promotion_is_approved_logged_and_reversible(taxonomy: Taxonomy):
  """Mapping a term approves it for this Corpus with provenance; rejecting undoes it."""
  aggregate, extract = reports()
  candidate = screen_unmapped_terms(
    aggregate, extract, [taxonomy], AliasOverlay()
  ).offered[0]

  overlay = promote_unmapped_term(
    AliasOverlay(), [taxonomy], candidate, "nervous-system-diseases"
  )

  entry = overlay.entries[0]
  assert (entry.alias, entry.status) == ("Neurologic disorders", "approved")
  assert entry.provenance.source == "unmapped_term"
  assert entry.provenance.citation_keys == ["alpha2020", "beta2021"]
  assert [step.action for step in entry.history] == ["proposed", "approved"]
  applied = apply_alias_overlay([taxonomy], overlay)[0]
  mapped = applied.resolve("neurologic disorders")
  assert mapped is not None
  assert mapped.id == "nervous-system-diseases"
  after = screen_unmapped_terms(aggregate, extract, [taxonomy], overlay)
  assert not after.offered

  reversed_overlay = reject_alias(
    overlay, "topic", "nervous-system-diseases", "Neurologic disorders"
  )

  assert [s.action for s in reversed_overlay.entries[0].history][-1] == "rejected"
  assert (
    apply_alias_overlay([taxonomy], reversed_overlay)[0].resolve("neurologic disorders")
    is None
  )
  again = screen_unmapped_terms(aggregate, extract, [taxonomy], reversed_overlay)
  assert [c.term for c in again.offered] == ["Neurologic disorders"]
  reapproved = promote_unmapped_term(
    reversed_overlay, [taxonomy], again.offered[0], "nervous-system-diseases"
  )
  assert [s.action for s in reapproved.entries[0].history] == [
    "proposed",
    "approved",
    "rejected",
    "approved",
  ]


def test_promoting_to_an_unknown_category_is_refused(taxonomy: Taxonomy):
  """The researcher must map the term onto a real category of its axis."""
  aggregate, extract = reports()
  candidate = screen_unmapped_terms(
    aggregate, extract, [taxonomy], AliasOverlay()
  ).offered[0]

  with pytest.raises(AliasOverlayError):
    promote_unmapped_term(AliasOverlay(), [taxonomy], candidate, "no-such-category")


def test_cli_lists_and_approves_unmapped_terms(tmp_path: Path, taxonomy_path: Path):
  """The CLI offers recurring terms and records an approved mapping in the overlay."""
  aggregate, extract = reports()
  corpus = tmp_path / "corpus"
  (corpus / "artifacts").mkdir(parents=True)
  (corpus / "artifacts" / NORMALIZED_FACTS_NAME).write_text(
    aggregate.model_dump_json(), encoding="utf-8"
  )
  (corpus / "artifacts" / EXTRACTIONS_NAME).write_text(
    extract.model_dump_json(), encoding="utf-8"
  )
  taxonomy_args = ["--taxonomy", str(taxonomy_path)]

  assert main(["aliases", "suggest-unmapped", str(corpus), *taxonomy_args]) == 0
  code = main(
    [
      "aliases",
      "approve-unmapped",
      str(corpus),
      "topic",
      "nervous-system-diseases",
      "neurologic disorders",
      *taxonomy_args,
    ]
  )

  assert code == 0
  raw = json.loads((corpus / "judgments" / "alias_overlay.json").read_text())
  assert [(e["alias"], e["status"]) for e in raw["entries"]] == [
    ("Neurologic disorders", "approved")
  ]
  refused = main(
    [
      "aliases",
      "approve-unmapped",
      str(corpus),
      "topic",
      "nervous-system-diseases",
      "pheochromocytoma",
      *taxonomy_args,
    ]
  )
  assert refused != 0


def test_promoting_a_mapped_term_records_its_papers(taxonomy_path: Path):
  """Copying a mapped term to the shared file keeps the Papers it came from."""
  taxonomy = load_taxonomy(taxonomy_path)
  aggregate, extract = reports()
  candidate = screen_unmapped_terms(
    aggregate, extract, [taxonomy], AliasOverlay()
  ).offered[0]
  overlay = promote_unmapped_term(
    AliasOverlay(), [taxonomy], candidate, "nervous-system-diseases"
  )

  promote_alias(
    overlay, taxonomy_path, "topic", "nervous-system-diseases", "Neurologic disorders"
  )

  topic = load_taxonomy(taxonomy_path).topic_by_id("nervous-system-diseases")
  assert topic is not None
  assert topic.aliases == ["Neurologic disorders"]
  assert topic.alias_sources == {
    "Neurologic disorders": (
      "unmapped term recurring in 2 Papers (alpha2020, beta2021)"
    )
  }
