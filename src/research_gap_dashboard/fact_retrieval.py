"""
Per-question retrieval of extracted facts for Conversational Analytics.

The general chat thread cannot carry every Paper's Extraction (hundreds of facts
with their Evidence), yet without them it can't answer "what methods did Templin
use?". So each question pulls in only the facts relevant to it, under a size
budget, and the model answers from those plus the Corpus list.

Matching is deterministic keyword matching over each fact's statement, its
verbatim Evidence passage, and the taxonomy categories its phrases were
normalized onto (spec #67: no embeddings and no new dependency until research
ticket #60 says otherwise). Three cues decide relevance:

- **Paper names.** A question naming a Paper (its citation key, the key's name
  part, or its first author's surname) is restricted to that Paper's facts.
  Co-authors don't name a Paper: one senior author sits on most of a Corpus.
- **Field cues.** Words like "methods" or "findings" favour that Extraction field.
- **Content words.** Every non-stopword (field cues included) is matched against
  the fact text, each hit weighted by how rare the word is across the Corpus's
  facts (inverse document frequency), so "prospective" counts for more than
  "patient" in a clinical Corpus. A field cue adds a fixed bonus.

A question with no cue that matches anything retrieves nothing; the facts are
never padded with unrelated ones, so an out-of-Corpus question reaches the model
with nothing to lean on and it says so. Ties are broken by Paper order, then
field order, so the same question always yields the same context and the LLM
cache stays warm (CODING_STANDARDS.md > Stages are reproducible).
"""

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping

from pydantic import BaseModel

# Extraction fields in the order an Extraction lists them (mirrors
# `extract._FIELD_NAMES`); ties break this way.
FIELD_ORDER = (
  "research_question",
  "methods",
  "populations",
  "datasets",
  "key_findings",
  "limitations",
  "future_work",
)

# Rendered-character budget for the facts block, and the cap on one passage. The
# budget keeps a question's context to a few thousand tokens however large the
# Corpus; the clip stops one long passage from crowding out the rest. A clipped
# passage stays a verbatim prefix, marked with an ellipsis.
DEFAULT_BUDGET_CHARS = 12_000
PASSAGE_CLIP_CHARS = 400

# Bonus for a fact in a field the question asks about ("methods", "findings").
# Equal to the weight of a content word found in about 58% of facts, so a rare
# keyword hit outranks a bare field match while a common one doesn't.
FIELD_CUE_WEIGHT = 1.0

# Words that point at an Extraction field. They steer the field choice and are
# still matched as content, since "registry" is also something a fact can say.
_FIELD_CUES: dict[str, tuple[str, ...]] = {
  "research_question": ("question", "aim", "objective", "purpose", "hypothesis"),
  "methods": (
    "method",
    "methodology",
    "design",
    "approach",
    "technique",
    "measure",
    "measured",
    "assess",
    "assessed",
    "analysis",
    "analyse",
    "analyze",
  ),
  "populations": (
    "population",
    "sample",
    "participant",
    "subject",
    "cohort",
    "patient",
    "who",
  ),
  "datasets": ("dataset", "data", "database", "registry", "source"),
  "key_findings": (
    "finding",
    "find",
    "found",
    "result",
    "conclude",
    "conclusion",
    "show",
    "showed",
    "outcome",
  ),
  "limitations": ("limitation", "weakness", "caveat", "shortcoming", "limit"),
  "future_work": ("future", "next", "further", "recommend", "recommendation"),
}

_STOPWORD_TEXT = frozenset(
  """
  a about above across after again against all also am an and any are as at be
  because been before being between both but by can could did do does doing done
  each either else ever few for from had has have having he her here hers him his
  how i if in into is it its itself just me more most my no nor not of off on once
  only or other our out over own same she should so some such than that the their
  them then there these they this those through to too under until up us use used
  using very was we were what when where which while whom why will with within
  would you your paper papers study studies studied studying corpus article tell
  give list say said report reported describe described explain discuss many much
  one ones did does there anything something
  """.split()
)

_TOKEN = re.compile(r"[a-z0-9]+")
_FACT_REF = re.compile(r"([a-z_]+)\[(\d+)\]")

# Word stems shorter than this only match exactly; longer ones also match words
# they prefix ("random" won't, "randomi" for "randomized/randomised" will).
_PREFIX_MIN = 5


class ExtractedFact(BaseModel):
  """
  One Extraction fact as chat grounding needs it, with its verbatim Evidence.

  `terms` holds the taxonomy category labels (and the Paper's original phrases)
  the Aggregate stage mapped this fact onto, so a question worded in taxonomy
  vocabulary still finds a fact phrased differently in the Paper.
  """

  citation_key: str
  field: str
  statement: str
  passage: str = ""
  section: str = ""
  terms: list[str] = []


def parse_fact_ref(fact_ref: str) -> tuple[str, int] | None:
  """Split a NormalizedFacts `fact_ref` ('methods[0]' or '[methods[0]]')."""
  match = _FACT_REF.search(fact_ref)
  return (match.group(1), int(match.group(2))) if match else None


def _fold(text: str) -> str:
  """Lowercase and strip accents so 'Stähli' matches the key 'stahli2022a'."""
  decomposed = unicodedata.normalize("NFKD", text)
  return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _stem(word: str) -> str:
  """Reduce a plural to its singular, the only inflection worth undoing here."""
  if len(word) > 4 and word.endswith("ies"):
    return word[:-3] + "y"
  if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
    return word[:-1]
  return word


def _tokens(text: str) -> list[str]:
  """Split text into folded, singularised word tokens."""
  return [_stem(token) for token in _TOKEN.findall(_fold(text))]


# Question words are stemmed before comparison, so the cue and stopword lists
# are too ("analysis" stems to "analysi", "does" to "doe").
_CUE_STEMS = {
  field: frozenset(_stem(cue) for cue in cues) for field, cues in _FIELD_CUES.items()
}
_STOPWORDS = frozenset(_stem(word) for word in _STOPWORD_TEXT)


def paper_aliases(citation_key: str, authors: Iterable[str]) -> set[str]:
  """
  Return the words a question may use to name one Paper.

  Its citation key, the key's leading name part ('templin' of 'templin2019'),
  and its first author's surname. Only the first author counts: a co-author's
  name would pull in every Paper a prolific senior author sits on.
  """
  key = _fold(citation_key)
  aliases = {key}
  name_part = re.match(r"[a-z]+", key)
  if name_part and len(name_part.group(0)) >= 3:
    aliases.add(name_part.group(0))
  first_author = next(iter(authors), "")
  surname = _TOKEN.findall(_fold(first_author))
  if surname and len(surname[-1]) >= 3:
    aliases.add(surname[-1])
  return aliases


def _word_matches(query: str, words: set[str]) -> bool:
  """Report whether a query stem occurs in a word set, exactly or as a prefix."""
  if query in words:
    return True
  if len(query) < _PREFIX_MIN:
    return False
  return any(
    word.startswith(query) or (len(word) >= _PREFIX_MIN and query.startswith(word))
    for word in words
  )


def render_fact(fact: ExtractedFact) -> str:
  """Render one fact and its clipped verbatim Evidence as a prompt line."""
  label = fact.field.replace("_", " ")
  line = f"- [{fact.citation_key} \u2014 {label}] {fact.statement}"
  passage = fact.passage.strip()
  if passage:
    if len(passage) > PASSAGE_CLIP_CHARS:
      passage = passage[:PASSAGE_CLIP_CHARS].rstrip() + "\u2026"
    section = f" ({fact.section})" if fact.section else ""
    line += f'\n  Evidence{section}: "{passage}"'
  return line


def _field_rank(field: str) -> int:
  """Position of a field in Extraction order; unknown fields sort last."""
  return FIELD_ORDER.index(field) if field in FIELD_ORDER else len(FIELD_ORDER)


def retrieve_facts(
  question: str,
  pool: list[ExtractedFact],
  aliases: Mapping[str, set[str]],
  *,
  follow_up_of: str = "",
  budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> list[ExtractedFact]:
  """
  Select the facts relevant to one question, most relevant first, within budget.

  `aliases` maps each citation key to the words that name it (`paper_aliases`).
  `follow_up_of` is the researcher's previous question: when this one names no
  Paper ("and what did they find?"), the Papers named there carry over. The
  rendered selection (`render_fact` lines) never exceeds `budget_chars`.
  """
  words = _tokens(question)
  named = named_papers(question, aliases, follow_up_of=follow_up_of)
  fields = {
    field for field, cues in _CUE_STEMS.items() if any(word in cues for word in words)
  }
  content = _content_words(words, aliases)
  fact_words = [_fact_words(fact) for fact in pool]
  weights = _rarity_weights(content, fact_words)

  scored = [
    (_score(fact, fact_words[position], fields, weights), position, fact)
    for position, fact in enumerate(pool)
    if not named or fact.citation_key in named
  ]

  # A named Paper with nothing matching the cues still brings its facts, so the
  # model can say what that Paper does report instead of seeing nothing at all.
  if not named or any(score for score, _, _ in scored):
    scored = [item for item in scored if item[0] > 0]

  return _fit_budget(_interleave(scored, pool), budget_chars)


def _content_words(words: list[str], aliases: Mapping[str, set[str]]) -> list[str]:
  """Return the question words matched against fact text: no stopwords or names."""
  names = set().union(*aliases.values()) if aliases else set()
  return sorted(
    {word for word in words if len(word) >= 3 and word not in _STOPWORDS | names}
  )


def _fact_words(fact: ExtractedFact) -> set[str]:
  """Return the searchable words of a fact: statement, Evidence and categories."""
  return set(_tokens(" ".join([fact.statement, fact.passage, *fact.terms])))


def _rarity_weights(content: list[str], fact_words: list[set[str]]) -> dict[str, float]:
  """
  Weight each content word by its rarity across the pool, ln(1 + N / df).

  The 1 + keeps a word found in every fact (a Corpus's own topic) worth
  something, so asking about the topic itself still retrieves; a word in no fact
  gets no weight, since it can never hit.
  """
  weights: dict[str, float] = {}
  for word in content:
    df = sum(1 for words in fact_words if _word_matches(word, words))
    if df:
      weights[word] = math.log(1 + len(fact_words) / df)
  return weights


def _score(
  fact: ExtractedFact, words: set[str], fields: set[str], weights: dict[str, float]
) -> float:
  """Score a fact: the rarity weight of each content word it holds, plus a field cue."""
  hits = sum(weight for word, weight in weights.items() if _word_matches(word, words))
  return hits + (FIELD_CUE_WEIGHT if fact.field in fields else 0.0)


def named_papers(
  question: str, aliases: Mapping[str, set[str]], *, follow_up_of: str = ""
) -> set[str]:
  """
  Return the citation keys of the Papers a question names.

  When the question names none, the Papers its predecessor (`follow_up_of`)
  named carry over, so "and what did they find?" stays on the same Paper.
  """
  return _named_in(_tokens(question), aliases) or _named_in(
    _tokens(follow_up_of), aliases
  )


def _named_in(words: list[str], aliases: Mapping[str, set[str]]) -> set[str]:
  """
  Return the citation keys whose aliases occur among the words.

  A surname shared by several Papers ("ghadri" for ghadri2013 and ghadri2016)
  is narrowed by a year in the question when one matches a key ("Ghadri 2016").
  """
  present = set(words)
  named = {key for key, names in aliases.items() if names & present}
  years = {word for word in present if re.fullmatch(r"(19|20)\d\d", word)}
  dated = {key for key in named if any(year in key for year in years)}
  return dated or named


def _interleave(
  scored: list[tuple[float, int, ExtractedFact]], pool: list[ExtractedFact]
) -> list[ExtractedFact]:
  """
  Order candidates by score, spreading equal scores across Papers.

  Within one score, each Paper's best fact comes before any Paper's second, so a
  cross-Paper question isn't answered from whichever Paper happens to be first.
  """
  paper_order: dict[str, int] = {}
  for fact in pool:
    paper_order.setdefault(fact.citation_key, len(paper_order))

  def within_paper(item: tuple[float, int, ExtractedFact]) -> tuple[float, int, int]:
    score, position, fact = item
    return (-score, _field_rank(fact.field), position)

  rank: dict[int, int] = {}
  seen: dict[tuple[str, float], int] = {}
  for item in sorted(scored, key=within_paper):
    score, position, fact = item
    slot = (fact.citation_key, score)
    rank[position] = seen.get(slot, 0)
    seen[slot] = rank[position] + 1

  ordered = sorted(
    scored,
    key=lambda item: (
      -item[0],
      rank[item[1]],
      paper_order[item[2].citation_key],
      _field_rank(item[2].field),
      item[1],
    ),
  )
  return [fact for _, _, fact in ordered]


def _fit_budget(facts: list[ExtractedFact], budget_chars: int) -> list[ExtractedFact]:
  """Keep facts in order while their rendered lines fit the character budget."""
  kept: list[ExtractedFact] = []
  used = 0
  for fact in facts:
    cost = len(render_fact(fact)) + 1
    if used + cost > budget_chars:
      continue
    kept.append(fact)
    used += cost
  return kept
