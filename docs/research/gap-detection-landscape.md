# Gap-detection landscape: how other tools find gaps, and what we borrow

Question: which mechanisms do existing gap-finding tools use, how does the
Research Gap Dashboard compare, and what should we adopt? The starting point was
a secondary summary the user brought in. Each claim below is marked
**verified** (checked against a primary source) or **unverified** (secondary
only, not to be cited as fact).

## Mechanisms and our position

| Mechanism | Tool | Our equivalent | Gap |
| --- | --- | --- | --- |
| Expected-vs-observed co-occurrence | Mine the Gap! | Coverage Matrix + ADR 0008 L1 | Plan confirmed; low power at our N (below) |
| Declared limitations, recurrence = signal | GapMiner; GAPMAP explicit gaps | Unanswered Limitation | Recurrence blocked by singleton grouping (#54) |
| Vocabulary normalisation | (general guidance) | Aggregate → taxonomies; unmapped terms | Narrow taxonomy (#56) creates false gaps |
| Two-hop bridges (A–B–C, no A–C) | Swanson ABC/AnC, LBDiscover | Knowledge Gap checks only the direct cell | **Missing** → bridge layer |
| Implicit gaps, Toulmin-structured | GAPMAP / TABI | ADR 0008 L5 + Audit Basis | Adopt the output structure + few-shot |
| Two independent witnesses (stat. empty ∩ declared) | (DIY pass in the summary) | None: cell and limitation gaps are separate | **Missing** → intersection layer |
| Adversarial saturation check | Paperguide | None: in-Corpus only | **Missing** → saturation check (needs a decision) |
| Citation topology | Connected Papers / Research Rabbit | Citation network graph | Built; same weakness on recent work |
| Latent semantics / semantic typing | LSI, BITOLA | ADR 0008 L3 (research pending); typed axes | Covered by `gap-detection-layers.md` Q3 |
| Missing code implementations | paper-gap | n/a | Out of scope |

## Verified claims

- **Mine the Gap!** Peng, Bonifield & Smalheiser, *Gaps within the Biomedical
  Literature: Initial Characterization and Assessment of Strategies for
  Discovery*, Front. Res. Metr. Anal. 2017, doi:10.3389/frma.2017.00003.
  MeSH terms in >1% of a PubMed query's articles are compared pairwise. A pair
  **predicted (under independence) to co-occur in ≥10 articles but observed in
  none** is a gap. Gap-filling articles were cited more heavily and were **61%
  more likely** to appear in multidisciplinary high-impact journals. The
  abstract also names **four gap types**: (a) byproducts of MeSH indexing rules,
  (b) gaps with little biological meaning, (c) "low-hanging fruit", (d) gaps
  across disciplines that don't talk to each other. (a) and (b) are pseudo-gap
  classes we should reject (ADR 0008 L5; our analogue of (a) is taxonomy
  artifacts, #56).
- **GAPMAP.** Salem, White, Bada & Hunter, *GAPMAP: Mapping Scientific
  Knowledge Gaps in Biomedical Literature Using Large Language Models*,
  arXiv:2510.25055; code at **`github.com/lhunter-lab/GAPMAP`**. (The summary
  said `UCDenver-ccp`, which is wrong.) Explicit vs implicit gaps, around 1,500
  documents across four datasets, closed-weight (OpenAI) and open-weight (Llama,
  Gemma 2) models. **TABI** = Toulmin-Abductive Bucketed Inference: *Claim,
  Grounds, Warrant, confidence Bucket*. Per the repo README, **few-shot examples
  materially improve TABI; zero-shot tends to be vague**, and sentence-aligned
  chunking of about 1K words is safe and often raises recall. Human-in-the-loop
  is recommended.
- **LBDiscover.** CRAN package (doi:10.32614/cran.package.lbdiscover):
  retrieval from PubMed/NCBI, entity extraction, co-occurrence networks,
  discovery models **ABC, AnC, LSI, BITOLA**, visualisation.

## Unverified (secondary only; do not cite as fact)

- GAPMAP's author survey (83.3% of gaps judged true, 65% of future directions
  valid) and "Llama-3.3-70B best F1". These are not in the abstract or README;
  check the full paper first.
- Swanson 1986 replication detail (27 bridge terms incl. blood viscosity,
  platelet aggregation). Swanson's Raynaud–fish-oil case is well known, but this
  specific replication was not located.
- GapMiner and paper-gap: no repository found by search.
- Paperguide's adversarial disproval step: vendor description only.

## The small-N caveat (important for ADR 0008 L1)

Mine the Gap's rule needs an expected count of ≥10 across thousands of
articles. Our Corpora hold 10–75 Papers, so almost no cell can reach that
expectation. At our scale an expected-vs-observed test can **rank** cells (an
empty cell between two fat margins ranks above one with a thin margin) but has
**too little power to gate** on its own. The corroborating layers below matter
more for us than for PubMed-scale tools.

## What we adopt

1. **Two-witness intersection.** Cross-link cell gaps with limitation groups
   that mention their categories. Statistically empty *and* declared by authors
   → boost confidence and label it. Empty but never mentioned → downgrade
   ("usually empty for a reason"). Depends on #54.
2. **Bridge categories (ABC/AnC).** For an empty A×C cell, list the B categories
   linked to both, and require ≥2 bridges (AnC) for higher confidence. This is
   deterministic over the existing matrix, and it feeds Tier 3 and Audit Basis.
3. **TABI-shaped L5 output with few-shot prompts.** Claim = gap statement,
   Grounds = Evidence passages, Warrant = one inspectable reasoning sentence,
   Bucket = confidence. Minimal Study is labelled a *suggestion*, since finding
   a gap and finding a feasible study are different problems.
4. **Limitation-type buckets.** Classify declared limitations by kind (e.g.
   sample size, population, design, follow-up, setting) so recurrence within a
   kind is visible.
5. **Saturation check.** An outward-looking search per gap. It crosses the
   in-Corpus boundary, so it is settled by its own grill (see ADR 0008
   addendum and the ADR that grill produces).

Credit for borrowed ideas belongs in README acknowledgments; none of these
sources is cited in product output.
