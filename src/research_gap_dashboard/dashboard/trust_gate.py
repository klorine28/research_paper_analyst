"""
The soft Trust gate: nobody reads gaps without facing the denominator (issue #77).

Explore, Judge, Grow and Write stay locked while Papers have fallen out of the
Corpus, until the researcher either fixes the fallout or acknowledges the
denominator ("these gaps come from 10 of 15 Papers"). It is soft: one
unrecoverable PDF never blocks a Corpus forever, and the incompleteness banner
stays on every page whatever is acknowledged (ADR 0006).

The acknowledgement is a researcher-review overlay under `judgments/`, like gap
verdicts (ADR 0003). It records the Corpus state it acknowledged, so it holds
only while that state does: once a Paper is added, recovered or loses facts the
state differs and the gate asks again. A complete Corpus passes without one.

The gate only closes once the extract stage has written its artifact: before
that the fallout is not known yet, there are no gaps to read, and the gated
pages show the run buttons that build them (ADR 0007). A Corpus that is
still being built is "unbuilt", and open.

Plain functions with no Streamlit dependency; like every dashboard read model it
imports no pipeline stage (ADR 0002).
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from research_gap_dashboard.dashboard.artifacts import has_extractions
from research_gap_dashboard.dashboard.attention import (
  IncompletenessBanner,
  build_attention,
)
from research_gap_dashboard.dashboard.judgments import JUDGMENTS_DIR

ACKNOWLEDGEMENT_NAME = "trust-acknowledgement.json"

GateState = Literal["unbuilt", "complete", "acknowledged", "locked"]


class CorpusState(BaseModel):
  """
  The denominator an acknowledgement is keyed to.

  Papers reached out of the total, plus the Papers that reached it missing some
  facts: a change in any of them changes what the gaps rest on.
  """

  reached_papers: int
  total_papers: int
  papers_with_dropped_facts: int

  @classmethod
  def of(cls, banner: IncompletenessBanner) -> "CorpusState":
    """Read the state off the incompleteness banner."""
    return cls(
      reached_papers=banner.reached_artifact,
      total_papers=banner.total_papers,
      papers_with_dropped_facts=banner.papers_with_dropped_facts,
    )


class DenominatorAcknowledgement(CorpusState):
  """The researcher's acknowledgement of one Corpus state, and when it was made."""

  acknowledged_at: datetime

  def acknowledges(self, state: CorpusState) -> bool:
    """Say whether this acknowledgement was made for exactly this Corpus state."""
    return CorpusState.model_validate(self.model_dump()) == state


class TrustGate(CorpusState):
  """Whether the gated steps are open, and the denominator to state if not."""

  state: GateState

  @property
  def is_open(self) -> bool:
    """True when the Corpus is complete or its current state is acknowledged."""
    return self.state != "locked"


def _acknowledgement_path(corpus_root: Path) -> Path:
  return corpus_root / JUDGMENTS_DIR / ACKNOWLEDGEMENT_NAME


def _load_acknowledgement(corpus_root: Path) -> DenominatorAcknowledgement | None:
  path = _acknowledgement_path(corpus_root)
  if not path.is_file():
    return None
  return DenominatorAcknowledgement.model_validate_json(
    path.read_text(encoding="utf-8")
  )


def build_trust_gate(corpus_root: Path) -> TrustGate:
  """Read whether a Corpus's gated steps are open, from its files on disk."""
  banner = build_attention(corpus_root).banner
  current = CorpusState.of(banner)
  state: GateState
  if banner.is_complete:
    state = "complete"
  elif not has_extractions(corpus_root):
    state = "unbuilt"
  else:
    record = _load_acknowledgement(corpus_root)
    acknowledged = (
      record is not None and CorpusState.model_validate(record.model_dump()) == current
    )
    state = "acknowledged" if acknowledged else "locked"
  return TrustGate(**current.model_dump(), state=state)


def acknowledge_denominator(corpus_root: Path) -> DenominatorAcknowledgement:
  """Record that the researcher accepts the Corpus's current denominator."""
  current = CorpusState.of(build_attention(corpus_root).banner)
  record = DenominatorAcknowledgement(
    **current.model_dump(), acknowledged_at=datetime.now(timezone.utc)
  )
  path = _acknowledgement_path(corpus_root)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
  return record
