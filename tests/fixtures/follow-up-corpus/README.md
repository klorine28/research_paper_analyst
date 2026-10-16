# Follow-up fixture Corpus

Four synthetic Papers (no PDFs) for the follow-up matcher (issue #55). A 2016
retrospective single-centre study says a prospective multicentre cohort is
needed, and a 2021 Paper is one: that limitation must be recognised as
addressed. A second 2016 Paper never recorded emotional triggers, and no later
Paper studies them: that control must stay an Unanswered Limitation. A 2019 case
report is a later Paper that addresses neither.

`llm-responses.json` holds the grouping and follow-up completions, recorded
live and keyed by request fingerprint. The model cites ids in the bracketed
`[key:field[index]]` form it sees in the prompt; read literally, that form
matched no fact, which is why no limitation was ever addressed on a real
Corpus. Rebuild the artifacts and re-record with
`uv run scripts/record_follow_up_fixture.py` after the limitation prompts or
the fixture change.
