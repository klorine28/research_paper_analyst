"""
Behavior of per-question fact retrieval for Conversational Analytics (issue #97).

Retrieval is a pure function: a question and the Corpus's extracted facts in,
the facts relevant to that question out, within a size budget. These tests pin
the three question shapes the spec names (a methods question about one Paper, a
cross-Paper question, and an out-of-Corpus question) plus the budget and the
determinism the cache depends on.
"""

from research_gap_dashboard.fact_retrieval import (
  ExtractedFact,
  named_papers,
  paper_aliases,
  render_fact,
  retrieve_facts,
)


def _fact(
  key: str,
  field: str,
  statement: str,
  *,
  passage: str | None = None,
  terms: list[str] | None = None,
) -> ExtractedFact:
  """Build one extracted fact, its passage echoing its statement by default."""
  return ExtractedFact(
    citation_key=key,
    field=field,
    statement=statement,
    passage=passage if passage is not None else f"{statement} (from {key}).",
    section="methods",
    terms=terms or [],
  )


_POOL = [
  _fact("templin2019", "methods", "Resting-state functional MRI of the brain"),
  _fact("templin2019", "populations", "15 patients with takotsubo syndrome"),
  _fact("templin2019", "key_findings", "Reduced limbic connectivity in patients"),
  _fact("ghadri2016", "methods", "Derivation of a diagnostic score in a registry"),
  _fact(
    "ghadri2016",
    "populations",
    "Patients from the International Takotsubo Registry",
    terms=["Prospective Studies"],
  ),
  _fact("kato2019", "populations", "A single woman aged 70 with biventricular TTS"),
  _fact("kato2019", "methods", "Case report with echocardiography"),
]

_ALIASES = {
  "templin2019": paper_aliases("templin2019", ["Christian Templin", "Carina Klein"]),
  "ghadri2016": paper_aliases("ghadri2016", ["Jelena Rima Ghadri"]),
  "kato2019": paper_aliases("kato2019", ["Ken Kato", "Jelena Rima Ghadri"]),
}


def _selected(question: str) -> list[tuple[str, str]]:
  """Retrieve over the shared pool and return (citation key, field) pairs."""
  facts = retrieve_facts(question, _POOL, _ALIASES)
  return [(fact.citation_key, fact.field) for fact in facts]


def test_aliases_are_the_key_its_name_part_and_the_first_author_surname():
  """A Paper is named by its key, the key's name part, or its first author."""
  assert paper_aliases("stahli2022a", ["Barbara Elisabeth Stähli", "M Other"]) == {
    "stahli2022a",
    "stahli",
  }
  assert "other" not in paper_aliases("stahli2022a", ["Barbara Stähli", "M Other"])


def test_methods_question_about_one_paper_selects_that_papers_methods():
  """'What methods did Templin use?' pulls Templin's methods, not other Papers'."""
  selected = _selected("What methods did Templin use?")

  assert selected[0] == ("templin2019", "methods")
  assert {key for key, _ in selected} == {"templin2019"}


def test_a_paper_named_without_a_field_brings_all_its_facts():
  """Naming a Paper with no other cue pulls that Paper's facts in field order."""
  assert _selected("Tell me about templin2019") == [
    ("templin2019", "methods"),
    ("templin2019", "populations"),
    ("templin2019", "key_findings"),
  ]


def test_co_authors_do_not_name_a_paper():
  """Ghadri is first author of ghadri2016 only, so kato2019 is not pulled in."""
  assert {key for key, _ in _selected("What population did Ghadri study?")} == {
    "ghadri2016"
  }


def test_cross_paper_question_selects_matching_facts_across_papers():
  """'Which Papers studied women?' matches facts by keyword over every Paper."""
  assert _selected("Which Papers studied a woman?") == [("kato2019", "populations")]


def test_cross_paper_field_question_interleaves_papers():
  """A field-only question spreads its picks across Papers, not one Paper first."""
  selected = _selected("Which populations were studied?")

  assert selected == [
    ("templin2019", "populations"),
    ("ghadri2016", "populations"),
    ("kato2019", "populations"),
  ]


def test_normalized_terms_are_searched_too():
  """A question matching only a fact's taxonomy category still finds it."""
  assert _selected("Any prospective studies?") == [("ghadri2016", "populations")]


def test_out_of_corpus_question_selects_nothing():
  """A question the Corpus can't answer retrieves no facts, so none are padded in."""
  assert not _selected("What is the capital of France?")


def test_selection_stays_within_the_size_budget():
  """Rendered selected facts never exceed the character budget."""
  pool = [
    _fact(f"p{index:02d}", "methods", f"Registry analysis number {index}")
    for index in range(50)
  ]

  facts = retrieve_facts("registry", pool, {}, budget_chars=500)

  assert facts
  assert sum(len(render_fact(fact)) + 1 for fact in facts) <= 500


def test_long_passages_are_clipped_when_rendered():
  """One huge passage can't blow the budget on its own."""
  fact = _fact("p01", "methods", "Registry", passage="x" * 5000)

  assert len(render_fact(fact)) < 1000


def test_retrieval_is_deterministic():
  """The same question over the same pool yields the same facts in the same order."""
  assert _selected("patients registry") == _selected("patients registry")


def test_a_follow_up_keeps_the_paper_named_in_the_previous_question():
  """With no Paper named, the Papers of the previous question carry over."""
  facts = retrieve_facts(
    "And the findings?", _POOL, _ALIASES, follow_up_of="Tell me about Kato"
  )

  assert {fact.citation_key for fact in facts} == {"kato2019"}


def test_a_paper_named_now_overrides_the_previous_question():
  """Naming a new Paper switches to it, whatever the previous question named."""
  facts = retrieve_facts(
    "What methods did Templin use?", _POOL, _ALIASES, follow_up_of="About Kato?"
  )

  assert {fact.citation_key for fact in facts} == {"templin2019"}


def test_a_rare_keyword_outranks_a_bare_field_cue():
  """'Which cohorts were prospective?' ranks the prospective fact over any cohort."""
  pool = [
    _fact(f"p{index}", "populations", f"{index} patients with takotsubo")
    for index in range(4)
  ] + [_fact("p9", "methods", "A prospective design")]

  facts = retrieve_facts("Which cohorts were prospective?", pool, {})

  assert facts[0].citation_key == "p9"


def test_a_word_in_every_fact_still_retrieves():
  """Asking about the Corpus's own topic, found in every fact, still finds facts."""
  pool = [_fact(f"p{index}", "methods", "Takotsubo imaging") for index in range(3)]

  assert len(retrieve_facts("takotsubo", pool, {})) == 3


def test_a_year_narrows_a_surname_shared_by_several_papers():
  """'Ghadri 2016' names ghadri2016, not every Paper Ghadri first-authored."""
  aliases = {
    "ghadri2013": paper_aliases("ghadri2013", ["J Ghadri"]),
    "ghadri2016": paper_aliases("ghadri2016", ["J Ghadri"]),
  }

  assert named_papers("What did Ghadri 2016 find?", aliases) == {"ghadri2016"}
  assert named_papers("What did Ghadri find?", aliases) == {
    "ghadri2013",
    "ghadri2016",
  }


def test_cue_and_stop_words_survive_stemming():
  """'analysis' still cues methods and 'does' is still a stopword after stemming."""
  pool = [_fact("p1", "methods", "Survival model"), _fact("p1", "datasets", "Doe")]

  facts = retrieve_facts("What analysis does it run?", pool, {})

  assert [(fact.field, fact.statement) for fact in facts] == [
    ("methods", "Survival model")
  ]
