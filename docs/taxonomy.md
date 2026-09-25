# Topic taxonomy

The **topic taxonomy** is the controlled vocabulary of Topics the Coverage
Matrix hangs off. The Aggregate stage maps each Paper's raw extracted phrases
onto these Topics so Papers can be compared and empty cells can surface as
Knowledge Gaps (see `CONTEXT.md` > Coverage Matrix, Gap Type).

It is a plain, human-editable TOML file. A cardiology seed derived from MeSH
ships at [`taxonomies/cardiology.toml`](../taxonomies/cardiology.toml). It is a
*starting point*: prune Topics you do not care about and add your own.

## File format

```toml
[meta]
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

## Validating a file

The loader collects every problem it finds and reports them together, so a
hand-edit is fixed in one pass:

```shell
uv run research-gap-dashboard taxonomy path/to/taxonomy.toml   # or no path for the seed
```

It reports: missing or unparseable file, no topics, missing or unknown Topic
fields, duplicate ids, ambiguous aliases (one phrase mapping to two Topics),
parent references that do not resolve, and parent cycles.
