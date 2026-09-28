"""
Assembling the Paper Explainer view from on-disk per-Paper data.

These are plain functions with no Streamlit dependency: the layout layer stays
thin by asking here for finished data. The explain stage writes one explanation
file per Paper under `paper-data/` (see `explain`); this view pairs each Paper's
manifest metadata with its explanation so the page can show both registers next
to a link back to the Paper. Papers keep the manifest's order so the same
Corpus always lists them the same way.
"""

from pydantic import BaseModel

from research_gap_dashboard.dashboard.artifacts import (
  ManifestArtifact,
  PaperExplanationArtifact,
  PaperRecord,
)


class PaperMenuEntry(BaseModel):
  """One Paper in the Explainer's picker: a label and whether it is explained."""

  citation_key: str
  title: str
  year: int | None
  has_explanation: bool

  @property
  def label(self) -> str:
    """A stable, human label for the picker (title falls back to citation key)."""
    name = self.title or self.citation_key
    return f"{name} ({self.year})" if self.year is not None else name


class PaperMenuData(BaseModel):
  """Everything the Explainer's Paper picker shows, already computed."""

  entries: list[PaperMenuEntry]

  @property
  def paper_count(self) -> int:
    """How many Papers the Corpus holds."""
    return len(self.entries)

  @property
  def explained_count(self) -> int:
    """How many Papers already have an explanation to show."""
    return sum(1 for entry in self.entries if entry.has_explanation)


class PaperExplainerView(BaseModel):
  """One Paper explained in both registers, with the metadata to link back to it."""

  citation_key: str
  title: str
  year: int | None
  journal: str
  doi: str
  domain_explanation: str
  lay_explanation: str


def build_paper_menu(
  manifest: ManifestArtifact, explained_keys: set[str]
) -> PaperMenuData:
  """List every Paper in the Corpus, flagging which ones have an explanation."""
  entries = [
    PaperMenuEntry(
      citation_key=paper.citation_key,
      title=paper.title,
      year=paper.year,
      has_explanation=paper.citation_key in explained_keys,
    )
    for paper in manifest.papers
  ]
  return PaperMenuData(entries=entries)


def build_paper_explainer(
  paper: PaperRecord, explanation: PaperExplanationArtifact
) -> PaperExplainerView:
  """Pair a Paper's manifest metadata with its two-register explanation."""
  return PaperExplainerView(
    citation_key=paper.citation_key,
    title=paper.title,
    year=paper.year,
    journal=paper.journal,
    doi=paper.doi,
    domain_explanation=explanation.registers.domain_explanation,
    lay_explanation=explanation.registers.lay_explanation,
  )
