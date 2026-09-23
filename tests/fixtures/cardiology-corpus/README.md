# Cardiology fixture Corpus

Ten open-access cardiology papers laid out per
[`docs/corpus-layout.md`](../../../docs/corpus-layout.md). Every downstream
pipeline and dashboard test runs against this Corpus; the test suite never
goes to the network.

## Licensing

Every PDF here is **CC BY 4.0**, which permits redistribution with
attribution. `provenance.json` records, for each file: DOI, title, journal,
year, licence, the exact URL it was downloaded from, and its OpenAlex work ID.

Rebuild the Corpus with:

```shell
uv run --script scripts/fetch_fixture_corpus.py
```

The script keeps only works whose OpenAlex `best_oa_location.license` is
`cc-by` and whose PDF is under the size budget, so a rebuild cannot silently
introduce a non-redistributable paper.

## Contents

The Papers are from *Frontiers in Cardiovascular Medicine* and *PLOS ONE*
(2018–2021), selected by citation count on the topics heart failure, atrial
fibrillation, and myocardial infarction. They are test data: their topical
mix is not a curated review, and no analysis result over them should be read
as a finding about cardiology.
