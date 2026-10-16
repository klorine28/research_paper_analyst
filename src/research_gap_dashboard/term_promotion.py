"""
Offer a Corpus's recurring unmapped terms for promotion to taxonomy aliases.

Aggregate surfaces every phrase that fit no category as an unmapped term. Most
recurring ones are the field's own words for a category the taxonomy already
has ("neurologic disorders" for Nervous System Diseases), so mapping them
removes false gaps (ADR 0010). Many others are noise a model left behind:
numbers, units, dosages, statistics, citation fragments, author names, generic
words and filler phrases.

A term is offered only when it passes three deterministic checks:

- **verified Evidence**: the phrase occurs in the verified Evidence passage of
  one of that Paper's facts on the axis, so a phrase the model invented is
  never offered;
- **recurrence**: that holds in at least `MIN_PAPERS` distinct Papers;
- **stoplist**: it is no noise class (`noise_class`).

Nothing here maps a phrase on its own. The researcher chooses the category,
and `promote_unmapped_term` logs that choice in the Corpus's Alias Overlay as a
proposal approved by a human, with the Papers it came from. It is reversed like
any overlay alias (`reject_alias`), after which the term is offered again.
"""

import re
import unicodedata
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from research_gap_dashboard.aggregate import AXIS_FIELDS, AggregateReport
from research_gap_dashboard.extract import Evidence, Extraction, ExtractReport
from research_gap_dashboard.taxonomy import Axis, Taxonomy
from research_gap_dashboard.vocabulary import (
  AliasOverlay,
  AliasProvenance,
  AliasSuggestion,
  apply_alias_overlay,
  approve_alias,
  propose_aliases,
)

# ADR 0010: a term must recur in at least this many Papers to be offered.
MIN_PAPERS = 2

# Longer than this, a phrase is a sentence fragment, not a vocabulary term.
MAX_TERM_WORDS = 8

NoiseClass = Literal[
  "number",
  "unit",
  "dosage",
  "statistic",
  "citation",
  "author_name",
  "generic",
  "stopword_heavy",
  "too_long",
]

SetAsideReason = NoiseClass | Literal["already_mapped", "unverified", "single_paper"]

_STOPWORDS = frozenset(
  "a an the of in on at to for from with without by and or nor but as into onto "
  "over under between among within than then that this these those which who "
  "whom whose what when where while is are was were be been being has have had "
  "do does did not no its it their there per via vs versus after before during "
  "about against through".split()
)

# Words that name no subject on their own; a term made only of them is noise.
_GENERIC = frozenset(
  "patient patients study studies result results data analysis analyses group "
  "groups participant participants subject subjects outcome outcomes finding "
  "findings method methods sample samples population populations individual "
  "individuals people person persons case cases effect effects factor factors "
  "treatment treatments intervention interventions control controls research "
  "paper article literature evidence information measure measures measurement "
  "measurements variable variables approach model models time times number rate "
  "rates level levels use total other others various different several new "
  "significant important high higher low lower increased decreased association "
  "associations relationship relationships role impact characteristics setting "
  "settings condition conditions disease diseases health clinical medical "
  "baseline value values change changes year years day days".split()
)

_NUMBER_WORDS = frozenset(
  "zero one two three four five six seven eight nine ten eleven twelve twenty "
  "thirty forty fifty hundred thousand million first second third fourth fifth "
  "half once twice".split()
)

_STATISTIC_TERMS = frozenset(
  {
    "p value",
    "p-value",
    "odds ratio",
    "hazard ratio",
    "relative risk",
    "risk ratio",
    "confidence interval",
    "standard deviation",
    "standard error",
    "interquartile range",
    "mean",
    "median",
    "range",
    "percentage",
    "proportion",
  }
)

_DOSE_UNITS = r"mg|mcg|µg|μg|ug|ng|g|ml|iu|units?"
_OTHER_UNITS = (
  r"mmhg|mmol|µmol|μmol|mol|kcal|bpm|cm|mm|nm|ms|mv|kg|lb|dl|l|m|s|sec|min|mins|"
  r"minutes?|h|hr|hrs|hours?|days?|weeks?|months?|years?|°c|c"
)
_UNIT_TOKENS = frozenset(
  (_DOSE_UNITS + "|" + _OTHER_UNITS).replace("?", "").split("|")
  + ["unit", "minute", "hour", "day", "week", "month", "year", "kg/m2", "%"]
)

_NUMBER = r"\d+(?:[.,]\d+)?"
_DOSAGE = re.compile(
  rf"{_NUMBER}\s*(?:{_DOSE_UNITS})(?:\s*/\s*(?:kg|day|d|dl|l|h|min|m2))*(?![a-z])"
)
_MEASUREMENT = re.compile(rf"{_NUMBER}\s*(?:{_OTHER_UNITS})(?![a-z])")
_STATISTIC = re.compile(
  r"\bp\s*[<>=≤≥]"
  r"|\d\s*%"
  r"|±"
  r"|\b(?:ci|or|hr|rr|aor|ahr|sd|se|iqr|auc)\s*[=:]"
  r"|\b(?:95|99|90)\s*%?\s*(?:ci|confidence interval)\b"
  r"|\bn\s*=\s*\d"
)
_NUMERIC_TOKEN = re.compile(
  rf"^[<>≤≥~]?{_NUMBER}(?:\s*[-–]\s*{_NUMBER})?(?:st|nd|rd|th)?$"
)
_CITATION = re.compile(
  r"\bet\.?\s+al\b"
  r"|\[\s*\d+(?:\s*[-–,]\s*\d+)*\s*\]"
  r"|\b(?:fig|figs|figure|figures|table|tables|ref|refs|supplementary)\.?\s*s?\d"
  r"|\bdoi\b"
  r"|https?://"
)
_SURNAME = r"[A-Z][a-z'’-]+"
_AUTHOR_NAME = [
  # J. Smith, J.A. Smith
  re.compile(rf"^(?:[A-Z]\.\s*)+{_SURNAME}$"),
  # Smith, J. / Smith, J.A.
  re.compile(rf"^{_SURNAME},\s*(?:[A-Z]\.?\s*)+$"),
  # "Smith JA" is left out: it cannot be told from "Functional MRI".
  # Smith 2019, Smith and Jones (2020)
  re.compile(
    rf"^{_SURNAME}(?:\s+(?:and|&)\s+{_SURNAME})?,?\s+\(?(?:19|20)\d{{2}}[a-z]?\)?$"
  ),
]


class TermOccurrence(BaseModel):
  """One Paper whose verified Evidence holds an unmapped term."""

  citation_key: str
  evidence: Evidence = Field(description="The verified passage holding the term.")


class PromotionCandidate(BaseModel):
  """A recurring, verified, real unmapped term offered for mapping onto a category."""

  axis: Axis
  term: str = Field(description="The phrase as the first Paper wrote it.")
  occurrences: list[TermOccurrence] = Field(
    description="The Papers whose verified Evidence holds it, one passage each."
  )

  @property
  def citation_keys(self) -> list[str]:
    """Return the Papers the term recurs in."""
    return [o.citation_key for o in self.occurrences]

  @property
  def evidence(self) -> list[Evidence]:
    """Return one verified passage per Paper."""
    return [o.evidence for o in self.occurrences]


class SetAsideTerm(BaseModel):
  """An unmapped term not offered, and why, so the filter can be audited."""

  axis: Axis
  term: str
  citation_keys: list[str] = Field(
    description="The Papers that left it unmapped on this axis."
  )
  reason: SetAsideReason


class UnmappedTermScreen(BaseModel):
  """Every distinct unmapped term of a Corpus, either offered or set aside."""

  offered: list[PromotionCandidate]
  set_aside: list[SetAsideTerm]


def noise_class(term: str) -> NoiseClass | None:
  """
  Return the stoplist class a term falls in, or None for a real term.

  Deterministic and deliberately conservative about digits: "type 2 diabetes"
  and "COVID-19" are terms, while "5 mg", "p < 0.05" and "2019" are not. The
  classes are checked in order, so "Smith 2019" is a name, not a number.
  """
  text = " ".join(unicodedata.normalize("NFKC", term).split())
  folded = text.casefold()
  words = _words(folded)
  stopwords = sum(1 for w in words if w in _STOPWORDS)
  checks: list[tuple[NoiseClass, bool]] = [
    ("author_name", any(pattern.match(text) for pattern in _AUTHOR_NAME)),
    ("citation", bool(_CITATION.search(folded))),
    (
      "statistic",
      bool(_STATISTIC.search(folded)) or folded.strip(" .") in _STATISTIC_TERMS,
    ),
    ("dosage", bool(_DOSAGE.search(folded))),
    ("unit", bool(_MEASUREMENT.search(folded)) or _all_units(folded)),
    ("number", all(_is_number(w) for w in words)),
    ("stopword_heavy", stopwords * 2 > len(words)),
    (
      "generic",
      all(w in _GENERIC or _is_number(w) for w in words if w not in _STOPWORDS),
    ),
    ("too_long", len(words) > MAX_TERM_WORDS),
  ]
  return next((noise for noise, hit in checks if hit), None)


def screen_unmapped_terms(
  aggregate: AggregateReport,
  extractions: ExtractReport,
  taxonomies: Sequence[Taxonomy],
  overlay: AliasOverlay,
  *,
  min_papers: int = MIN_PAPERS,
) -> UnmappedTermScreen:
  """
  Sort a Corpus's distinct unmapped terms into offered and set aside.

  Terms are grouped per axis by a punctuation-tolerant fold, so "ST-segment
  elevation" and "ST segment elevation" count as one. A term the taxonomies
  (with the Corpus's active overlay aliases) now resolve is set aside as
  already mapped, so an approved term stops being offered until reversed.
  """
  by_key = {e.citation_key: e for e in extractions.extractions}
  applied = {t.meta.axis: t for t in apply_alias_overlay(taxonomies, overlay)}
  offered: list[PromotionCandidate] = []
  set_aside: list[SetAsideTerm] = []
  for group in _group_unmapped(aggregate):
    taxonomy = applied.get(group.axis)
    reason: SetAsideReason | None = noise_class(group.term)
    if reason is None and taxonomy and taxonomy.resolve(group.term) is not None:
      reason = "already_mapped"
    occurrences = [] if reason else _occurrences(group, by_key)
    if reason is None and not occurrences:
      reason = "unverified"
    elif reason is None and len(occurrences) < min_papers:
      reason = "single_paper"
    if reason is not None:
      set_aside.append(
        SetAsideTerm(
          axis=group.axis,
          term=group.term,
          citation_keys=sorted(k for k, _ in group.spellings),
          reason=reason,
        )
      )
      continue
    verified = {o.citation_key for o in occurrences}
    offered.append(
      PromotionCandidate(
        axis=group.axis,
        term=next(spelled for key, spelled in group.spellings if key in verified),
        occurrences=occurrences,
      )
    )

  offered.sort(key=lambda c: (-len(c.occurrences), c.axis, c.term.casefold()))
  set_aside.sort(key=lambda t: (t.reason, t.axis, t.term.casefold()))
  return UnmappedTermScreen(offered=offered, set_aside=set_aside)


class _TermGroup(BaseModel):
  """One distinct unmapped term on one axis, with each Paper's spelling of it."""

  axis: Axis
  canonical: str
  spellings: list[tuple[str, str]] = Field(
    description="(citation key, phrase as that Paper wrote it), one per Paper."
  )

  @property
  def term(self) -> str:
    """Return the phrase as the first Paper wrote it."""
    return self.spellings[0][1]


def _group_unmapped(aggregate: AggregateReport) -> list[_TermGroup]:
  """Group every Paper's unmapped terms by axis and folded phrase."""
  groups: dict[tuple[Axis, str], _TermGroup] = {}
  for paper in aggregate.normalized:
    for unmapped in paper.unmapped:
      canonical = _canonical(unmapped.original_term)
      if not canonical:
        continue
      group = groups.setdefault(
        (unmapped.axis, canonical),
        _TermGroup(axis=unmapped.axis, canonical=canonical, spellings=[]),
      )
      if all(key != paper.citation_key for key, _ in group.spellings):
        spelled = " ".join(unmapped.original_term.split())
        group.spellings.append((paper.citation_key, spelled))
  return list(groups.values())


def _occurrences(
  group: _TermGroup, extractions: dict[str, Extraction]
) -> list[TermOccurrence]:
  """Return the Papers whose verified Evidence on the axis holds the term."""
  occurrences: list[TermOccurrence] = []
  for citation_key, _ in group.spellings:
    extraction = extractions.get(citation_key)
    if extraction is None:
      continue
    evidence = _verified_evidence(extraction, group.axis, group.canonical)
    if evidence is not None:
      occurrences.append(TermOccurrence(citation_key=citation_key, evidence=evidence))
  return occurrences


def find_candidate(
  screen: UnmappedTermScreen, axis: Axis, term: str
) -> PromotionCandidate | None:
  """Return the offered candidate for this axis and phrase, if it is offered."""
  canonical = _canonical(term)
  return next(
    (c for c in screen.offered if c.axis == axis and _canonical(c.term) == canonical),
    None,
  )


def promote_unmapped_term(
  overlay: AliasOverlay,
  taxonomies: Sequence[Taxonomy],
  candidate: PromotionCandidate,
  category_id: str,
) -> AliasOverlay:
  """
  Record the researcher's mapping of an offered term onto one category.

  The term is logged as a proposed alias with its Papers as provenance, then
  approved, so the overlay keeps both steps. Approval refuses an unknown
  category, or a phrase another category already owns (`approve_alias`).
  """
  suggestion = AliasSuggestion(
    axis=candidate.axis,
    category_id=category_id,
    alias=candidate.term,
    provenance=AliasProvenance(
      source="unmapped_term", citation_keys=candidate.citation_keys
    ),
  )
  proposed = propose_aliases(overlay, [suggestion])
  return approve_alias(
    proposed, taxonomies, candidate.axis, category_id, candidate.term
  )


def _verified_evidence(
  extraction: Extraction, axis: Axis, canonical: str
) -> Evidence | None:
  """Return the first verified passage on this axis that holds the term."""
  needle = f" {canonical} "
  for field in AXIS_FIELDS[axis]:
    for fact in getattr(extraction.fields, field):
      if needle in f" {_canonical(fact.evidence.passage)} ":
        return fact.evidence
  return None


def _canonical(text: str) -> str:
  """Fold case, Unicode forms and punctuation, so a phrase matches its passage."""
  folded = unicodedata.normalize("NFKC", text).casefold()
  return " ".join(re.sub(r"[^\w]+", " ", folded).split())


def _words(folded: str) -> list[str]:
  """Split a folded term into words, keeping numbers and units whole."""
  return [w for w in re.split(r"[\s,;:()\[\]]+", folded) if w.strip(".-")]


def _is_number(word: str) -> bool:
  """Return whether a word is a number, numeric range, ordinal, or number word."""
  return bool(_NUMERIC_TOKEN.match(word)) or word.strip(".") in _NUMBER_WORDS


def _all_units(folded: str) -> bool:
  """Return whether a term is only unit symbols, such as "mg/kg" or "mmHg"."""
  parts = [p for p in re.split(r"[\s/·]+", folded) if p]
  return bool(parts) and all(p in _UNIT_TOKENS for p in parts)
