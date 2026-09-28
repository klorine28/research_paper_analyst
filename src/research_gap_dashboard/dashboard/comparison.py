"""
Assembling the Paper Comparison view from the on-disk artifacts.

The researcher picks 2-5 Papers and sees them side by side, field by field
(research question, methods, population, datasets, findings, limitations), with
agreements and differences highlighted and a mini Coverage Matrix of what none
of the selected Papers cover. A larger selection (up to 15 Papers) is split into
sets of at most five, each set summarized, and the summaries compared instead of
the raw Papers (CONTEXT.md > Coverage Matrix; docs/BRIEF.md, decision 8).

These are plain functions with no Streamlit dependency, and they import no
pipeline stage (ADR 0002): the layout layer asks here for finished data. Two
things this module is careful about, both from CODING_STANDARDS.md > Research
integrity:

- Agreement highlighting and blind spots are grounded in the shared Normalized
  Facts categories, never guessed from free text: two Papers "agree" on an axis
  only when the aggregate stage placed them on the same category.
- A blind spot is a category the wider Corpus studies that none of the selection
  covers, so the mini matrix states the Corpus it was computed against rather
  than implying the category does not exist.
"""

import math

from pydantic import BaseModel

from research_gap_dashboard.dashboard.artifacts import (
  ExtractedFactRecord,
  ExtractionFieldsRecord,
  ExtractionRecord,
  ExtractionsArtifact,
  ManifestArtifact,
  NormalizedFactsArtifact,
  NormalizedFactsRecord,
)

# A selection smaller than this cannot be compared; a larger one than the direct
# limit is summarized in chunks first (docs/BRIEF.md, decision 8).
MIN_SELECTION = 2
MAX_DIRECT = 5
MAX_SELECTION = 15
CHUNK_SIZE = MAX_DIRECT

# The four Coverage Matrix axes, in display order, with their human labels.
AXIS_LABELS: dict[str, str] = {
  "topic": "Topic",
  "method": "Method",
  "population": "Population",
  "dataset": "Dataset",
}

# The Extraction fields the comparison shows side by side, in display order, each
# paired with the Coverage Matrix axis that grounds its agreement highlighting
# (None for a free-text field with no shared taxonomy).
_FIELDS: tuple[tuple[str, str, str | None], ...] = (
  ("research_question", "Research question", None),
  ("methods", "Methods", "method"),
  ("populations", "Population", "population"),
  ("datasets", "Datasets", "dataset"),
  ("key_findings", "Findings", None),
  ("limitations", "Limitations", None),
)


class SelectionError(Exception):
  """Raised when a comparison is asked for over an unusable Paper selection."""


class ComparedPaper(BaseModel):
  """One selected Paper's identity, for labelling its column."""

  citation_key: str
  title: str
  year: int | None

  @property
  def label(self) -> str:
    """A stable, human label for the Paper (title falls back to citation key)."""
    name = self.title or self.citation_key
    return f"{name} ({self.year})" if self.year is not None else name


class CategoryCoverage(BaseModel):
  """One Coverage Matrix category and which compared units cover it."""

  category_id: str
  label: str
  covering_keys: list[str]


class AxisComparison(BaseModel):
  """
  One Coverage Matrix axis compared across the selection.

  Agreements are categories every compared unit covers; differences are covered
  by some but not all; blind spots are categories the Corpus studies that none
  of the selection covers (the mini Coverage Matrix).
  """

  axis: str
  axis_label: str
  agreements: list[CategoryCoverage]
  differences: list[CategoryCoverage]
  blind_spots: list[CategoryCoverage]

  @property
  def has_signal(self) -> bool:
    """Whether this axis contributes anything to show for the selection."""
    return bool(self.agreements or self.differences or self.blind_spots)


class UnitFieldFacts(BaseModel):
  """One compared unit's statements for one Extraction field."""

  key: str
  label: str
  statements: list[str]


class FieldComparison(BaseModel):
  """One Extraction field compared across the selection, unit by unit."""

  field_id: str
  label: str
  axis: str | None
  units: list[UnitFieldFacts]


class ChunkSummary(BaseModel):
  """
  One set of at most five Papers, summarized so its summary can be compared.

  A summary is a mechanical rollup, never an LLM guess: it unions the set's
  Normalized Facts categories per axis and keeps each Paper's field statements,
  so the comparison over summaries stays traceable to the Papers behind it.
  """

  index: int
  citation_keys: list[str]

  @property
  def label(self) -> str:
    """A stable label naming the set and the Papers it rolls up."""
    return f"Set {self.index + 1} ({', '.join(self.citation_keys)})"


class ComparisonResult(BaseModel):
  """Everything the Paper Comparison view shows, already computed."""

  citation_keys: list[str]
  papers: list[ComparedPaper]
  is_chunked: bool
  chunks: list[ChunkSummary]
  unit_labels: list[str]
  fields: list[FieldComparison]
  axes: list[AxisComparison]
  corpus_paper_count: int


def selection_error(selection: list[str], manifest: ManifestArtifact) -> str | None:
  """
  Report why a selection cannot be compared, or None when it can.

  A comparison needs between two and fifteen distinct Papers, all of which are in
  the Corpus. Returned as a message so the layout can show it rather than raise.
  """
  distinct = list(dict.fromkeys(selection))
  if len(distinct) < MIN_SELECTION:
    return f"Pick at least {MIN_SELECTION} Papers to compare."
  if len(distinct) > MAX_SELECTION:
    return f"Pick at most {MAX_SELECTION} Papers to compare in one view."
  corpus_keys = {paper.citation_key for paper in manifest.papers}
  missing = [key for key in distinct if key not in corpus_keys]
  if missing:
    return f"Not in this Corpus: {', '.join(missing)}."
  return None


def chunk_selection(
  citation_keys: list[str], chunk_size: int = CHUNK_SIZE
) -> list[list[str]]:
  """
  Split a selection into sets of at most ``chunk_size``, order preserved.

  Sets are made as even as possible so a 6-Paper selection becomes two sets of
  three rather than a set of five and a set of one; a selection that already
  fits in one set is returned unchanged.
  """
  count = len(citation_keys)
  if count <= chunk_size:
    return [list(citation_keys)]
  set_count = math.ceil(count / chunk_size)
  base, extra = divmod(count, set_count)
  chunks: list[list[str]] = []
  start = 0
  for index in range(set_count):
    size = base + (1 if index < extra else 0)
    chunks.append(list(citation_keys[start : start + size]))
    start += size
  return chunks


def build_comparison(
  selection: list[str],
  extractions: ExtractionsArtifact,
  normalized: NormalizedFactsArtifact,
  manifest: ManifestArtifact,
) -> ComparisonResult:
  """
  Compare the selected Papers field by field, with agreements and blind spots.

  Two to five Papers are compared directly; six to fifteen are split into sets of
  at most five, each set summarized, and the summaries compared. Raises
  SelectionError on a selection that cannot be compared.
  """
  error = selection_error(selection, manifest)
  if error is not None:
    raise SelectionError(error)

  keys = list(dict.fromkeys(selection))
  papers = _compared_papers(keys, manifest)
  extraction_by_key = {
    record.citation_key: record for record in extractions.extractions
  }
  categories_by_key = {record.citation_key: record for record in normalized.normalized}
  corpus_categories = _corpus_categories(normalized)

  is_chunked = len(keys) > MAX_DIRECT
  if is_chunked:
    chunks = [
      ChunkSummary(index=index, citation_keys=chunk_keys)
      for index, chunk_keys in enumerate(chunk_selection(keys))
    ]
    units = [
      _Unit(
        key=f"set-{chunk.index + 1}",
        label=chunk.label,
        member_keys=chunk.citation_keys,
      )
      for chunk in chunks
    ]
  else:
    chunks = []
    units = [
      _Unit(key=key, label=paper.label, member_keys=[key])
      for key, paper in zip(keys, papers, strict=True)
    ]

  fields = [
    _field_comparison(field_id, label, axis, units, extraction_by_key)
    for field_id, label, axis in _FIELDS
  ]
  axes = [
    _axis_comparison(axis, units, categories_by_key, corpus_categories)
    for axis in AXIS_LABELS
  ]

  return ComparisonResult(
    citation_keys=keys,
    papers=papers,
    is_chunked=is_chunked,
    chunks=chunks,
    unit_labels=[unit.label for unit in units],
    fields=fields,
    axes=axes,
    corpus_paper_count=len(manifest.papers),
  )


class _Unit(BaseModel):
  """A single thing being compared: one Paper directly, or a summarized set."""

  key: str
  label: str
  member_keys: list[str]


def _compared_papers(
  keys: list[str], manifest: ManifestArtifact
) -> list[ComparedPaper]:
  """Pair each selected key with its manifest metadata, keeping selection order."""
  by_key = {paper.citation_key: paper for paper in manifest.papers}
  return [
    ComparedPaper(
      citation_key=key,
      title=by_key[key].title,
      year=by_key[key].year,
    )
    for key in keys
  ]


def _corpus_categories(
  normalized: NormalizedFactsArtifact,
) -> dict[str, dict[str, str]]:
  """Map every axis to the categories any Corpus Paper was placed on: id -> label."""
  categories: dict[str, dict[str, str]] = {axis: {} for axis in AXIS_LABELS}
  for record in normalized.normalized:
    for assignment in record.assignments:
      if assignment.axis in categories:
        categories[assignment.axis].setdefault(
          assignment.category_id, assignment.category_label or assignment.category_id
        )
  return categories


def _field_comparison(
  field_id: str,
  label: str,
  axis: str | None,
  units: list[_Unit],
  extraction_by_key: dict[str, ExtractionRecord],
) -> FieldComparison:
  """Gather each compared unit's statements for one Extraction field."""
  unit_facts = [
    UnitFieldFacts(
      key=unit.key,
      label=unit.label,
      statements=_field_statements(field_id, unit.member_keys, extraction_by_key),
    )
    for unit in units
  ]
  return FieldComparison(field_id=field_id, label=label, axis=axis, units=unit_facts)


def _field_statements(
  field_id: str,
  member_keys: list[str],
  extraction_by_key: dict[str, ExtractionRecord],
) -> list[str]:
  """Collect one field's statements across a unit's member Papers, tagged by key."""
  statements: list[str] = []
  chunked = len(member_keys) > 1
  for key in member_keys:
    record = extraction_by_key.get(key)
    if record is None:
      continue
    facts = _fields_attr(record.fields, field_id)
    for fact in facts:
      text = fact.statement.strip()
      if not text:
        continue
      statements.append(f"[{key}] {text}" if chunked else text)
  return statements


def _fields_attr(
  fields: ExtractionFieldsRecord, field_id: str
) -> list[ExtractedFactRecord]:
  """Return the list of extracted facts for one field name."""
  return getattr(fields, field_id)


def _axis_comparison(
  axis: str,
  units: list[_Unit],
  categories_by_key: dict[str, NormalizedFactsRecord],
  corpus_categories: dict[str, dict[str, str]],
) -> AxisComparison:
  """Compare one axis across the units into agreements, differences, blind spots."""
  labels = dict(corpus_categories.get(axis, {}))
  covering: dict[str, list[str]] = {}
  for unit in units:
    for category_id, label in _unit_categories(axis, unit, categories_by_key):
      labels.setdefault(category_id, label)
      keys = covering.setdefault(category_id, [])
      if unit.key not in keys:
        keys.append(unit.key)

  unit_count = len(units)
  agreements: list[CategoryCoverage] = []
  differences: list[CategoryCoverage] = []
  for category_id, keys in covering.items():
    coverage = CategoryCoverage(
      category_id=category_id, label=labels[category_id], covering_keys=keys
    )
    if len(keys) == unit_count:
      agreements.append(coverage)
    else:
      differences.append(coverage)

  blind_spots = [
    CategoryCoverage(category_id=category_id, label=label, covering_keys=[])
    for category_id, label in labels.items()
    if category_id not in covering
  ]

  agreements.sort(key=lambda c: c.label)
  differences.sort(key=lambda c: c.label)
  blind_spots.sort(key=lambda c: c.label)
  return AxisComparison(
    axis=axis,
    axis_label=AXIS_LABELS[axis],
    agreements=agreements,
    differences=differences,
    blind_spots=blind_spots,
  )


def _unit_categories(
  axis: str,
  unit: _Unit,
  categories_by_key: dict[str, NormalizedFactsRecord],
) -> list[tuple[str, str]]:
  """Return the distinct (id, label) categories a unit covers on one axis."""
  found: dict[str, str] = {}
  for key in unit.member_keys:
    record = categories_by_key.get(key)
    if record is None:
      continue
    for assignment in record.assignments:
      if assignment.axis == axis:
        found.setdefault(
          assignment.category_id, assignment.category_label or assignment.category_id
        )
  return list(found.items())
