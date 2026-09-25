# Taxonomies

A **taxonomy** is the controlled vocabulary for one axis of the Coverage
Matrix. There are four axes, each with its own file:

| Axis | Seed | Fed by Extraction field(s) | Seeded from |
| --- | --- | --- | --- |
| `topic` | [`taxonomies/cardiology.toml`](../taxonomies/cardiology.toml) | all seven | MeSH "Heart Diseases" subtree (C14.280) |
| `method` | [`taxonomies/methods.toml`](../taxonomies/methods.toml) | `methods` | curated MeSH study characteristics and publication types |
| `population` | [`taxonomies/populations.toml`](../taxonomies/populations.toml) | `populations` | curated MeSH age groups, sex, species, care setting |
| `dataset` | [`taxonomies/datasets.toml`](../taxonomies/datasets.toml) | `datasets` | curated MeSH records, registries, databases |

The Aggregate stage maps each Paper's raw extracted phrases onto every axis's
categories so Papers can be compared, and so empty cells (Topic × Method,
Topic × Population, Topic × Dataset) can surface as Knowledge and Coverage Gaps
(see `CONTEXT.md` > Coverage Matrix, Gap Type).

Each is a plain, human-editable TOML file in the same format. The seeds are
*starting points*: prune categories you do not care about and add your own.

## File format

```toml
[meta]
axis = "topic"                 # optional: topic (default), method, population, or dataset
domain = "cardiology"          # required: the field this vocabulary covers
source = "MeSH 2025"           # required: where the seed came from
source_url = "https://meshb.nlm.nih.gov/treeView"   # optional
mesh_tree_root = "C14.280"     # optional; extra meta keys are allowed

[[topics]]
id = "takotsubo-cardiomyopathy"   # required: stable slug, unique in the file
label = "Takotsubo Cardiomyopathy"  # required: human-readable name
mesh_id = "D054549"               # required: MeSH descriptor id, or "" if hand-added
mesh_tree = "C14.280.238.906"     # optional: MeSH tree number (provenance)
parent = "cardiomyopathies"       # optional: id of a broader Topic
aliases = ["Broken Heart Syndrome", "Stress Cardiomyopathy"]  # optional synonyms
description = "..."               # optional scope note
```

- **Entries are `[[topics]]` on every axis**, so one loader serves all four;
  in a Method file each entry is a study design, in a Dataset file a data
  source, and so on.
- **`id`, `label`, `mesh_id` are required** on every Topic. A hand-added Topic
  with no MeSH origin sets `mesh_id = ""`.
- **`aliases`** are the phrases an extracted term may match on, in addition to
  the `id` and `label`. Matching is case-insensitive and whitespace-tolerant.
  The MeSH seed fills these from each descriptor's entry terms.
- **`parent`** builds a shallow hierarchy; it must name another Topic's `id`.
- **Unknown keys are errors.** A typo like `alias` instead of `aliases` is
  reported, not silently dropped.

## Provenance

The cardiology seed is derived from
[MeSH](https://meshb.nlm.nih.gov/) — the "Heart Diseases" subtree (tree number
`C14.280`), first level plus the "Cardiomyopathies" subtree where the fixture
corpus lives. Each Topic keeps its MeSH descriptor id and tree number so any
Topic traces back to its source.

Rebuild the seed (needs network; the test suite never does) with:

```shell
uv run --script scripts/build_cardiology_taxonomy.py
```

The Method, Population, and Dataset seeds are curated picks, since MeSH has no
single subtree for study designs or data sources. The curated labels live in
`scripts/build_axis_taxonomies.py`; each label is resolved against MeSH by
exact match, so every category keeps a real descriptor id, tree number, and
entry terms as aliases. Parents are taken from the MeSH tree where both ends
were picked. MeSH publication types (e.g. Randomized Controlled Trial) carry no
entry terms, so the script hand-adds a few common spellings ("RCT",
"randomised trial"); these are the only aliases that do not come from MeSH.
Rebuild them with:

```shell
uv run --script scripts/build_axis_taxonomies.py
```

## Normalizing onto the taxonomies

`research-gap-dashboard aggregate <corpus>` normalizes onto all four seeds by
default. To use your own files, pass `--taxonomy` once per axis; each file
names its axis in `[meta]`, and two files for one axis are rejected. An axis
you leave out produces no categories for that axis. Every assignment in
`artifacts/normalized_facts.json` records its axis, the original phrase, the
category, and the Extraction fact it came from (e.g. `methods[0]`) together
with that fact's Evidence. Phrases that fit no category are listed as unmapped,
per axis, so you know what to add to the taxonomy.

## Validating a file

The loader collects every problem it finds and reports them together, so a
hand-edit is fixed in one pass:

```shell
uv run research-gap-dashboard taxonomy path/to/taxonomy.toml   # or no path for the seed
```

It reports: missing or unparseable file, an unknown axis, no topics, missing or unknown Topic
fields, duplicate ids, ambiguous aliases (one phrase mapping to two Topics),
parent references that do not resolve, and parent cycles.
