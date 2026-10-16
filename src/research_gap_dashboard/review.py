"""
The L5 review: an LLM challenges each surviving Candidate Gap (ADR 0008 §1, §3).

Detect hands this module one candidate at a time, already through the
deterministic layers, as a `ReviewRequest`: what the gap says, why it ranked
where it did, the Evidence it rests on (each under an id), and what the Corpus
covers around it. One structured call (prompt `gap-review-v1`, three synthetic
examples) answers with a verdict:

- **reject**, naming one pseudo-gap class (generic upgrade, template reuse,
  pseudo-firstness, non-closable, low-value replication, taxonomy artifact,
  little meaning) and a one-sentence warrant; or
- **keep**, with a TABI-style card (GAPMAP): a Claim, Grounds (Evidence ids),
  a Warrant, an Audit Basis (what the Corpus covers around the gap, citing
  Evidence ids), a Minimal Study suggestion, and a bucket. `least_probable`
  tells detect to lower the candidate one level; nothing the model says raises
  one, so its self-assessment cannot inflate a gap.

The model only ever proposes Evidence ids; each is checked against the ids the
request supplied, and an unknown one is dropped and noted, so a review can cite
nothing outside the candidate's own (already verified, ADR 0004) Evidence. The
synthetic examples use a fictional condition and fictional citation keys, so no
real source can leak into a card. Calls go through the cached client (ADR
0001), keyed by prompt and prompt version, so a rerun on unchanged inputs is
free. Which candidates are reviewed, and what a verdict does to the trail, is
decided in `detect`.
"""

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from research_gap_dashboard.llm import (
  LlmClient,
  LlmResponseError,
  ModelTier,
  complete_model,
)

logger = logging.getLogger(__name__)

# Bump when the prompt, its examples, or the output shape change: it keys the
# LLM cache (ADR 0001).
REVIEW_PROMPT_VERSION = "gap-review-v1"

ATTEMPTS = 3

Verdict = Literal["keep", "reject"]
Bucket = Literal["more_probable", "least_probable"]
RejectionClass = Literal[
  "generic_upgrade",
  "template_reuse",
  "pseudo_firstness",
  "non_closable",
  "low_value_replication",
  "taxonomy_artifact",
  "little_meaning",
]

# The plain-words name of each class, for the trail and the dashboard.
REJECTION_CLASS_NAMES: dict[RejectionClass, str] = {
  "generic_upgrade": "a generic upgrade",
  "template_reuse": "template reuse",
  "pseudo_firstness": "pseudo-firstness",
  "non_closable": "not closable",
  "low_value_replication": "a low-value replication",
  "taxonomy_artifact": "a taxonomy artifact",
  "little_meaning": "of little meaning",
}


class ReviewEvidence(BaseModel):
  """One Evidence item the reviewer may cite, under the id it must cite it by."""

  evidence_id: str
  citation_key: str
  text: str = Field(description="What the item says: term or statement, and passage.")


class ReviewRequest(BaseModel):
  """Everything the reviewer sees about one candidate."""

  gap_id: str
  gap_type_name: str
  title: str
  explanation: str
  reasons: list[str] = Field(description="Each earlier layer's reason, in order.")
  evidence: list[ReviewEvidence]
  surroundings: list[str] = Field(
    default_factory=list,
    description="What the Corpus covers around the gap (e.g. neighbouring cells).",
  )


class AuditBasis(BaseModel):
  """What the Corpus covers around a kept gap, and the Evidence that shows it."""

  text: str
  evidence_ids: list[str]


class GapReview(BaseModel):
  """
  The validated L5 verdict on one candidate, as the artifact records it.

  Every id in `grounds` and `audit_basis` is one the request supplied; `notes`
  records each correction made to the model's answer.
  """

  prompt_version: str
  verdict: Verdict
  rejection_class: RejectionClass | None = None
  claim: str | None = None
  grounds: list[str] = Field(default_factory=list)
  warrant: str = ""
  audit_basis: AuditBasis | None = None
  minimal_study: str | None = None
  bucket: Bucket | None = None
  notes: list[str] = Field(default_factory=list)


class _ProposedAuditBasis(BaseModel):
  """The model's Audit Basis, before its ids are validated."""

  text: str = Field(description="What the Corpus covers around the gap.")
  evidence_ids: list[str] = Field(
    default_factory=list, description="Ids of the Evidence items that show it."
  )


class _ProposedReview(BaseModel):
  """The model's review of one candidate, before its ids are validated."""

  verdict: Verdict
  rejection_class: RejectionClass | None = Field(
    default=None, description="Required when verdict is 'reject'; else null."
  )
  claim: str | None = Field(
    default=None, description="Kept gaps: the gap, stated as one claim."
  )
  grounds: list[str] = Field(
    default_factory=list, description="Kept gaps: ids of the supporting Evidence."
  )
  warrant: str = Field(
    description="One sentence: the reasoning from the grounds to the verdict."
  )
  audit_basis: _ProposedAuditBasis | None = Field(
    default=None, description="Kept gaps: what the Corpus covers around the gap."
  )
  minimal_study: str | None = Field(
    default=None, description="Kept gaps: the smallest study that could close it."
  )
  bucket: Bucket | None = Field(
    default=None, description="Kept gaps: more_probable or least_probable."
  )


def review_gap(
  request: ReviewRequest, client: LlmClient, *, tier: ModelTier = "default"
) -> GapReview | None:
  """
  Review one candidate and validate the answer; None if no usable answer came.

  A failed review leaves the candidate as the deterministic layers left it; the
  caller records that it went unreviewed.
  """
  try:
    proposal = complete_model(
      client,
      _prompt(request),
      _ProposedReview,
      prompt_version=REVIEW_PROMPT_VERSION,
      tier=tier,
      attempts=ATTEMPTS,
      accept=_complete,
    )
  except (LlmResponseError, ValidationError) as error:
    logger.warning("Review of %s returned no usable answer: %s", request.gap_id, error)
    return None
  return _validated(proposal, request)


def _complete(proposal: _ProposedReview) -> bool:
  """Accept a reject that names its class, or a keep that buckets itself."""
  if proposal.verdict == "reject":
    return proposal.rejection_class is not None
  return proposal.bucket is not None and bool(proposal.claim)


def _validated(proposal: _ProposedReview, request: ReviewRequest) -> GapReview:
  """Keep only supplied Evidence ids and a shape consistent with the verdict."""
  known = [item.evidence_id for item in request.evidence]
  notes: list[str] = []
  if proposal.verdict == "reject":
    if proposal.rejection_class is None:
      notes.append("the review rejected the candidate without naming a class")
    return GapReview(
      prompt_version=REVIEW_PROMPT_VERSION,
      verdict="reject",
      rejection_class=proposal.rejection_class,
      warrant=proposal.warrant,
      notes=notes,
    )

  if proposal.rejection_class is not None:
    notes.append(
      f"ignored rejection class '{proposal.rejection_class}' on a kept candidate"
    )
  bucket = proposal.bucket
  if bucket is None:
    # A keep that does not rate itself is read as the cautious bucket, so a
    # missing answer can never leave a candidate higher than a hedged one.
    bucket = "least_probable"
    notes.append("the review gave no bucket; read as least_probable")
  audit_basis = None
  if proposal.audit_basis is not None:
    audit_basis = AuditBasis(
      text=proposal.audit_basis.text,
      evidence_ids=_known_ids(
        proposal.audit_basis.evidence_ids, known, "audit-basis Evidence id", notes
      ),
    )
  return GapReview(
    prompt_version=REVIEW_PROMPT_VERSION,
    verdict="keep",
    claim=proposal.claim,
    grounds=_known_ids(proposal.grounds, known, "ground id", notes),
    warrant=proposal.warrant,
    audit_basis=audit_basis,
    minimal_study=proposal.minimal_study,
    bucket=bucket,
    notes=notes,
  )


def _known_ids(
  proposed: list[str], known: list[str], what: str, notes: list[str]
) -> list[str]:
  """Keep each proposed id the request supplied, once; note every one dropped."""
  kept: list[str] = []
  for raw in proposed:
    evidence_id = _unbracketed(raw)
    if evidence_id not in known:
      notes.append(f"dropped unknown {what} '{raw}'")
    elif evidence_id not in kept:
      kept.append(evidence_id)
  return kept


def _unbracketed(reference: str) -> str:
  """Strip the outer brackets a model copies from the prompt's `[id]` rendering."""
  reference = reference.strip()
  outer = re.fullmatch(r"\[(.+:.+\[\d+\])\]", reference)
  return outer.group(1) if outer else reference


# The three synthetic examples (ADR 0008 §3: few-shot, since zero-shot output is
# vague). The condition, the categories, and the citation keys are fictional,
# so nothing here can be mistaken for, or cited as, a real source.
_EXAMPLES = """\
EXAMPLE 1
CANDIDATE: Coverage Gap: Marrowfen syndrome x Randomized Controlled Trial
EXPLANATION: No Paper in the Corpus combines Marrowfen syndrome (topic) with \
Randomized Controlled Trial (method), although 9 of 12 Papers cover Marrowfen \
syndrome and 3 of 12 cover Randomized Controlled Trial.
LAYER REASONS:
- About 2.2 of 12 Papers would combine them if independent; 0 do.
EVIDENCE:
- [alder2019:methods[0]] (alder2019) We retrospectively reviewed 84 patients \
with Marrowfen syndrome.
- [brook2021:methods[0]] (brook2021) Patients with chronic veldt fever were \
randomized to drug or placebo.
- [cass2020:limitations[1]] (cass2020) No randomized evidence yet guides \
treatment of Marrowfen syndrome.
AROUND THE GAP:
- Marrowfen syndrome x Retrospective Studies: 7 Paper(s)
ANSWER: {"verdict": "keep", "rejection_class": null, "claim": "No trial in the \
Corpus randomizes treatment for Marrowfen syndrome, although trials exist for \
neighbouring conditions.", "grounds": ["alder2019:methods[0]", \
"cass2020:limitations[1]"], "warrant": "The Corpus studies Marrowfen syndrome \
only retrospectively while an author names the missing randomized evidence, so \
the empty cell reflects a real hole rather than chance.", "audit_basis": \
{"text": "Seven Papers study Marrowfen syndrome with retrospective designs; \
randomized designs appear only for other conditions.", "evidence_ids": \
["alder2019:methods[0]", "brook2021:methods[0]"]}, "minimal_study": "A \
two-arm pilot randomized trial of the standard therapy against usual care in \
adults with Marrowfen syndrome.", "bucket": "more_probable"}

EXAMPLE 2
CANDIDATE: Knowledge Gap: Marrowfen syndrome x Night Shift Work
EXPLANATION: No Paper in the Corpus combines Marrowfen syndrome with Night \
Shift Work, although 9 of 12 Papers cover Marrowfen syndrome and 2 of 12 \
cover Night Shift Work.
LAYER REASONS:
- About 1.5 of 12 Papers would combine them if independent; 0 do.
EVIDENCE:
- [dune2018:research_question[0]] (dune2018) We describe, for the first time, \
Marrowfen syndrome in a nurse working night shifts.
- [elm2022:populations[0]] (elm2022) Factory workers on rotating night shifts.
AROUND THE GAP:
- Night Shift Work x Sleep Disorders: 2 Paper(s)
ANSWER: {"verdict": "reject", "rejection_class": "pseudo_firstness", "claim": \
null, "grounds": [], "warrant": "The only novelty is being the first to report \
the syndrome in one more occupational group, which names no question a study \
would answer.", "audit_basis": null, "minimal_study": null, "bucket": null}

EXAMPLE 3
CANDIDATE: Unanswered Limitation: Long-term recurrence unknown
EXPLANATION: 2 Paper(s) in the Corpus (alder2019, cass2020) state this \
limitation, and none of the 3 later Paper(s) addressed it.
LAYER REASONS:
- 3 later Paper(s) in the Corpus were checked for a follow-up.
EVIDENCE:
- [alder2019:limitations[2]] (alder2019) Follow-up ended at 6 months, so \
recurrence could not be assessed.
- [cass2020:future_work[0]] (cass2020) Recurrence beyond the first year \
should be studied.
AROUND THE GAP:
- Later Papers checked: brook2021, fenn2022, gale2023
ANSWER: {"verdict": "keep", "rejection_class": null, "claim": "Recurrence of \
Marrowfen syndrome beyond one year is unmeasured in the Corpus.", "grounds": \
["alder2019:limitations[2]", "cass2020:future_work[0]"], "warrant": "Two \
independent Papers stop follow-up within a year and later Papers did not extend \
it, but only three later Papers were checked, so the hole may be narrow.", \
"audit_basis": {"text": "Follow-up in the Corpus ends at 6 to 12 months.", \
"evidence_ids": ["alder2019:limitations[2]"]}, "minimal_study": "A registry \
follow-up of an existing cohort to 3 years, recording recurrence.", "bucket": \
"least_probable"}
"""

_INSTRUCTIONS = """\
You review one candidate research gap that a tool found in a small corpus of \
research papers. Challenge it: decide whether it is a real gap worth a \
researcher's attention, using only the evidence given.

Reject it (verdict "reject") if it is one of these pseudo-gaps, naming the class:
- generic_upgrade: "do the same with a bigger sample / more centres / newer \
tools", with no specific question.
- template_reuse: a stock gap pattern ("no study in country X", "no study in \
group Y") applied without a reason the combination matters.
- pseudo_firstness: the only novelty is being first in a trivial sense.
- non_closable: no feasible study could ever answer it (ethically, practically, \
or in principle).
- low_value_replication: repeating settled work where a new result would not \
change understanding or practice.
- taxonomy_artifact: an artifact of how the papers were indexed (overlapping, \
nested, or near-synonymous categories), not two distinct subjects.
- little_meaning: the combination makes little scientific or clinical sense.

Otherwise keep it (verdict "keep") and write:
- claim: the gap, as one plain claim.
- grounds: the ids of the evidence items that support it.
- warrant: one sentence of reasoning from the grounds to the claim.
- audit_basis: what the corpus covers around the gap, with the ids of the \
evidence items that show it.
- minimal_study: the smallest study that could close it; a suggestion, not a \
claim that it is feasible.
- bucket: "more_probable" if the evidence makes the gap convincing, \
"least_probable" if it is plausible but weakly supported.

For a reject, give the class and a one-sentence warrant, and leave the other \
fields null or empty. Cite evidence only by the bracketed ids shown, without \
the brackets; never cite any other source. Do not call anything statistically \
significant. The examples below are fictional and only show the format.
"""


def _prompt(request: ReviewRequest) -> str:
  """Build the review prompt for one candidate."""
  reasons = "\n".join(f"- {reason}" for reason in request.reasons) or "- none"
  evidence = "\n".join(
    f"- [{item.evidence_id}] ({item.citation_key}) {item.text}"
    for item in request.evidence
  )
  surroundings = "\n".join(f"- {line}" for line in request.surroundings) or "- none"
  return (
    f"{_INSTRUCTIONS}\n{_EXAMPLES}\n"
    "NOW REVIEW THIS CANDIDATE\n"
    f"CANDIDATE ID: {request.gap_id}\n"
    f"CANDIDATE: {request.gap_type_name}: {request.title}\n"
    f"EXPLANATION: {request.explanation}\n"
    f"LAYER REASONS:\n{reasons}\n"
    f"EVIDENCE:\n{evidence}\n"
    f"AROUND THE GAP:\n{surroundings}\n"
    "ANSWER:"
  )
