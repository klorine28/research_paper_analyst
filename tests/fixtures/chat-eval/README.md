# Chat evaluation set

Measures Conversational Analytics' citation accuracy and grounding (issue #99).
`tests/test_chat_eval.py` replays every question here through `answer_question`
on recorded completions, offline, and judges each answer with
`research_gap_dashboard.chat_eval.evaluate_answer`.

| File | What it holds |
| --- | --- |
| `context.json` | The grounding context: the Papers and fact pool (Extraction facts with verbatim Evidence) of the cardiology fixture Corpus (`../cardiology-corpus`, CC BY 4.0), extracted and aggregated live. No Candidate Gaps, so gap-detection changes don't churn the eval. |
| `cases.json` | The questions. Each has `expected_citations` (Corpus Papers a correct answer cites) and `required_evidence` (substrings the answer must quote, which the retrieved context must carry verbatim), or `"abstain": true` for a question the Corpus can't answer. `history` holds earlier turns for a follow-up. |
| `llm-responses.json` | The recorded chat completions, keyed by request fingerprint (prompt, schema, tier, prompt version), for `RecordedLlmClient`. |
| `takotsubo-15.live-cases.json` | Cases for the live end-to-end check on `corpora/takotsubo-15` (not committed, not run by the tests). |

A case fails when the answer cites a key that isn't a Corpus Paper (in its
citations or its prose), cites a Corpus Paper the question retrieved nothing
from, misses an expected citation, or doesn't quote required Evidence; an
`abstain` case fails if it cites anything or doesn't say the Corpus doesn't
answer the question.

## Re-recording (manual, needs `ANTHROPIC_API_KEY`)

The suite never calls the API. When the chat prompt, retrieval, the context, or
the cases change, the prompt fingerprints change and the eval test fails with
"no recorded completion". Re-record on purpose:

```shell
uv run scripts/record_chat_eval.py
```

It answers every case live, rewrites `llm-responses.json`, and logs each
verdict; it exits non-zero if a case fails, so read the log before committing.

To rebuild `context.json` from a freshly extracted copy of the cardiology
Corpus (`ingest`, `parse`, `extract`, `aggregate` on a scratch copy), then
re-record:

```shell
uv run scripts/record_chat_eval.py --snapshot path/to/extracted-cardiology-copy
uv run scripts/record_chat_eval.py
```

## Live check on a real Corpus

```shell
uv run scripts/record_chat_eval.py --live corpora/takotsubo-15 \
  --cases tests/fixtures/chat-eval/takotsubo-15.live-cases.json
```

Builds the same context the Analytics page does (Candidate Gaps included),
answers each case with the live model, logs verdicts, and writes nothing.
