# Saturation fixture Corpus

Six synthetic Papers (no PDFs) placed so `detect` cards a Knowledge Gap the
wider literature plainly fills (Heart Failure x Myocardial Ischemia) and a
control Coverage Gap nothing closes (Carcinoid Heart Disease x Mendelian
Randomization Analysis). Two Unanswered Limitation cards are added by hand: one
later trials followed up (SGLT2 inhibitors in heart failure with preserved
ejection fraction) and a control nobody has (spaceflight and carcinoid heart
disease). `source-responses.json` holds the OpenAlex and PubMed answers to all
four saturation checks, recorded live and keyed by URL, so the tests run
offline. Rebuild with `uv run scripts/record_saturation_fixture.py`.
`llm-responses.json` holds the LLM screening completions for the filled gap's
check, recorded live and keyed by request fingerprint; re-record with
`uv run scripts/record_saturation_screening.py` after the screening prompt or
the source responses change.
