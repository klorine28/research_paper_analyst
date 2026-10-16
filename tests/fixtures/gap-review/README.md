# `gap-review-v1` fixture

The L5 review's recorded answers (issue #120), replayed by
`tests/test_detect_review.py` through `RecordedLlmClient`, offline.

| Path | What it holds |
| --- | --- |
| `corpus/artifacts/` | A synthetic Corpus of ten fictional Papers (`tako1`–`tako5` study Takotsubo retrospectively, `hf1`–`hf5` study heart failure in randomized trials), with no limitation statements. Exactly three cells reach Medium or above, so exactly three are reviewed. |
| `llm-responses.json` | The three review answers, keyed by request fingerprint: one keep, one `pseudo_firstness` reject, and one `least_probable` keep citing a ground id (`zz9999:methods[0]`) that is not in its Evidence. |

The answers are authored, not recorded live: a live model gives an unknown
ground id only by chance, and the test needs one every time.

## Re-recording

When the review prompt, its output shape, or detect's description of a
candidate changes, the fingerprints change and the test fails with "No recorded
LLM response". Rewrite both files on purpose (no API key needed):

```shell
uv run scripts/record_gap_review_fixture.py
```

Bump `REVIEW_PROMPT_VERSION` in `review.py` when the prompt changes, so cached
live answers are not reused.
