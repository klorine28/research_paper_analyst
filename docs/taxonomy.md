# Taxonomies

A **taxonomy** is the controlled vocabulary for one axis of the Coverage
Matrix. There are four axes, each with its own file:

| Axis | Seed | Fed by Extraction field(s) | Seeded from |
| --- | --- | --- | --- |
| `topic` | [`taxonomies/cardiology.toml`](../taxonomies/cardiology.toml) | all eight | MeSH "Heart Diseases" subtree (C14.280) plus curated cardiac procedures (E01/E02/E04) |
| `method` | [`taxonomies/methods.toml`](../taxonomies/methods.toml) | `study_design`, `methods` | curated MeSH study characteristics and publication types; study design only (ADR 0011) |
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
alias_sources = { "Stress Cardiomyopathy" = "MeSH entry term of D054549 (Takotsubo Cardiomyopathy)" }  # optional
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
- **`alias_sources`** records where an alias came from, keyed by the alias;
  `aliases promote` fills it. Every key must be one of the Topic's aliases.
- **`parent`** builds a shallow hierarchy; it must name another Topic's `id`.
  When it is missing, the parent is derived from the Topic in the same file
  whose `mesh_tree` is the longest whole-segment prefix of this Topic's
  (`C14.280.238` is the parent of `C14.280.238.906`). Aggregate records the
  hierarchy in the NormalizedFacts artifact, and detect's taxonomy roll-up
  (ADR 0008 L2) rejects a category paired with its own ancestor and lowers a
  gap that a Paper placed only on a broader category may cover. A file with
  neither `parent` nor `mesh_tree` is flat, and the roll-up does nothing.
- **Unknown keys are errors.** A typo like `alias` instead of `aliases` is
  reported, not silently dropped.

## Provenance

The cardiology seed is derived from
[MeSH](https://meshb.nlm.nih.gov/) — the "Heart Diseases" subtree (tree number
`C14.280`), first level plus the "Cardiomyopathies" subtree where the fixture
corpus lives. Each Topic keeps its MeSH descriptor id and tree number so any
Topic traces back to its source.

### Procedure seed (ADR 0011)

Procedures and treatments are Topics, never Methods, so procedure combinations
surface as Knowledge Gaps (Topic × Topic). After the disease subtree, the
cardiology seed lists a curated set of cardiac diagnostic and therapeutic
procedures from MeSH's procedure branches. The labels live in `PROCEDURES` in
`scripts/build_cardiology_taxonomy.py`; each is resolved against MeSH by exact
match, keeps its descriptor id and its first E01/E02/E04 tree number, and takes
its entry terms as aliases (minus any already claimed by another Topic).

| Branch | Topic id | MeSH id | Tree number |
| --- | --- | --- | --- |
| E01 Diagnosis | `heart-function-tests` | D006334 | E01.370.370.380 |
| | `cardiac-imaging-techniques` | D057791 | E01.370.350.130 |
| | `echocardiography` | D004452 | E01.370.350.130.750 |
| | `echocardiography-doppler` | D015150 | E01.370.350.130.750.220 |
| | `echocardiography-transesophageal` | D017548 | E01.370.350.130.750.235 |
| | `echocardiography-stress` | D025401 | E01.370.350.130.750.228 |
| | `electrocardiography` | D004562 | E01.370.370.380.240 |
| | `electrocardiography-ambulatory` | D015716 | E01.370.370.380.240.230 |
| | `coronary-angiography` | D017023 | E01.370.350.130.625 |
| | `cardiac-catheterization` | D006328 | E01.370.370.380.140 |
| | `computed-tomography-angiography` | D000072226 | E01.370.350.350.810.335 |
| | `magnetic-resonance-imaging-cine` | D019028 | E01.370.350.825.500.510 |
| | `myocardial-perfusion-imaging` | D055414 | E01.370.350.130.875 |
| | `positron-emission-tomography` | D049268 | E01.370.350.350.800.700 |
| | `exercise-test` | D005080 | E01.370.370.380.250 |
| | `pericardiocentesis` | D020519 | E01.370.225.998.329.620 |
| E02 Therapeutics | `thrombolytic-therapy` | D015912 | E02.319.913 |
| | `cardiac-pacing-artificial` | D002304 | E02.331.200 |
| | `cardiac-resynchronization-therapy` | D058406 | E02.331.200.500 |
| | `catheter-ablation` | D017115 | E02.808.750.500 |
| | `electric-countershock` | D004554 | E02.331.350 |
| | `cardiopulmonary-resuscitation` | D016887 | E02.365.647.110 |
| | `extracorporeal-membrane-oxygenation` | D015199 | E02.880.301 |
| | `cardiac-rehabilitation` | D000072038 | E02.760.169.063.500.185 |
| | `left-atrial-appendage-closure` | D000097546 | E02.148.442.582 |
| | `angioplasty-balloon-coronary` | D015906 | E02.148.050.060.100 |
| E04 Surgical Procedures | `assisted-circulation` | D001243 | E04.050 |
| | `intra-aortic-balloon-pumping` | D007423 | E04.050.215.400 |
| | `percutaneous-coronary-intervention` | D062645 | E04.100.814.529.968 |
| | `coronary-artery-bypass` | D001026 | E04.100.376.719.332 |
| | `heart-transplantation` | D016027 | E04.100.376.475 |
| | `heart-valve-prosthesis-implantation` | D019918 | E04.100.376.485 |
| | `transcatheter-aortic-valve-replacement` | D065467 | E04.100.376.485.500 |

The tree number shown is the lowest-sorting one in E01/E02/E04, so a few
procedures that MeSH files under several branches (pericardiocentesis, coronary
balloon angioplasty) are listed under the branch that sorts first. Prune any
procedure the Corpus does not need; lab assays and statistical techniques are
deliberately on no axis.

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

The Method axis is study design only (ADR 0011). It is fed by the Paper's one
plain-words `study_design` fact plus its `methods` facts; any phrase there that
is a procedure, treatment, lab assay, or statistical technique is listed under
`not_study_design` instead of unmapped, so it is never suggested as a Method
alias. A Method phrase the Topic taxonomy recognises (e.g. "transthoracic
echocardiography") is always set aside this way, whatever the model said.

## Expanding aliases per Corpus (ADR 0010)

A false gap often means the taxonomy lacks the field's words. The `aliases`
command widens the vocabulary from MeSH, kept per Corpus in an **Alias
Overlay** at `<corpus>/judgments/alias_overlay.json`:

```shell
uv run research-gap-dashboard aliases suggest-mesh path/to/corpus   # needs network
uv run research-gap-dashboard aliases list path/to/corpus --status proposed
uv run research-gap-dashboard aliases approve path/to/corpus topic cardiomyopathies "Myocardiopathies"
uv run research-gap-dashboard aliases reject path/to/corpus topic cardiomyopathies "Myocardiopathies"
uv run research-gap-dashboard aliases promote path/to/corpus topic cardiomyopathies "Myocardiopathies"
```

- `suggest-mesh` reads each MeSH-backed category's **entry terms** and the
  labels of its direct **child descriptors** that are not categories
  themselves (so a narrower descriptor rolls up onto its parent). Each is logged
  as *proposed* with its provenance (source kind, MeSH id, label, tree number).
  A phrase the taxonomy already resolves, or that MeSH would give to two
  categories, is not offered. Re-running never re-offers a decided alias.
- Nothing maps until **approved**, and an approval applies only to that
  Corpus: `aggregate <corpus>` normalizes onto the shared files plus that
  Corpus's approved aliases. Approving a phrase another category already owns
  is refused. `reject` declines a proposal or reverses an approval; every step
  is logged with its date.
- `promote` copies an approved alias into the shared taxonomy file (the one
  given with `--taxonomy` for that axis, or the shipped seed), with its
  provenance in `alias_sources`, and re-validates the file; it then applies to
  every Corpus. A promoted alias is removed by editing that file.

### Promoting recurring unmapped terms

The Corpus's own unmapped terms are the other source. After `extract` and
`aggregate`:

```shell
uv run research-gap-dashboard aliases suggest-unmapped path/to/corpus
uv run research-gap-dashboard aliases approve-unmapped path/to/corpus topic nervous-system-diseases "neurologic disorders"
```

- `suggest-unmapped` offers a term only if (1) the phrase occurs in the
  **verified Evidence** passage of one of that Paper's facts on the axis, so a
  phrase the model invented is never offered; (2) that holds in **at least 2
  Papers**; and (3) it passes a deterministic **stoplist**: no numbers, units,
  dosages, statistics, citation fragments, author names, generic words
  ("patients", "study", "results"), stopword-heavy phrases, or phrases longer
  than 8 words. A term the taxonomy (with the Corpus's approved aliases)
  already resolves is not offered. Every other term is tallied by the reason it
  was set aside, so the filter can be audited. Nothing is written.
- `approve-unmapped` is the researcher's mapping of one offered term onto a
  category. It is logged in the Alias Overlay as proposed then approved, with
  the Papers it came from as provenance, and applies to this Corpus only.
  `reject` reverses it (the term is offered again), and `promote` copies it to
  the shared file like any approved alias.

## Validating a file

The loader collects every problem it finds and reports them together, so a
hand-edit is fixed in one pass:

```shell
uv run research-gap-dashboard taxonomy path/to/taxonomy.toml   # or no path for the seed
```

It reports: missing or unparseable file, an unknown axis, no topics, missing or unknown Topic
fields, duplicate ids, ambiguous aliases (one phrase mapping to two Topics),
alias sources for phrases that are not aliases,
parent references that do not resolve, and parent cycles.
