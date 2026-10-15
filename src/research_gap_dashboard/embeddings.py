"""
The embedding boundary: one seam between the pipeline and a sentence embedder.

Stages that compare texts by meaning take an `Embedder` (`embed(texts) ->
vectors`), injected the way the LLM client is, so the suite runs on recorded
vectors and never downloads a model. The default implementation wraps
`sentence-transformers` with a biomedical model (PubMedBERT); it lives behind
the optional `embeddings` extra, and `build_embedder` returns None when the
extra is not installed, so every caller must keep working without vectors.

The model id and revision come from configuration and travel with the embedder,
so the stage that uses it can record them in its artifact's provenance. What
deliberately isn't here: what a stage does with the vectors (each stage owns
its use and its threshold) and any on-disk cache (the model runs locally and
is pinned to a revision, so a rerun reproduces the same vectors).
"""

import importlib
import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Defaults; overridable from the environment (see .env.example). The revision
# pins the model's weights so vectors, and so merge hints, are reproducible.
DEFAULT_EMBEDDING_MODEL = "NeuML/pubmedbert-base-embeddings"
DEFAULT_EMBEDDING_REVISION = "b79526d6ef3645e0df4530322e266f24c829f5ef"
# Cosine similarity at or above which two limitation statements from different
# Papers are offered to the grouping step as a merge hint (spec #112).
DEFAULT_MERGE_THRESHOLD = 0.65

Vector = list[float]


class EmbeddingConfigError(Exception):
  """Raised when embedding configuration is present but invalid."""


class EmbeddingConfig(BaseModel):
  """Embedding settings, read once from the environment and validated."""

  model_id: str = DEFAULT_EMBEDDING_MODEL
  revision: str = DEFAULT_EMBEDDING_REVISION
  merge_threshold: float = Field(default=DEFAULT_MERGE_THRESHOLD, gt=0.0, le=1.0)

  @classmethod
  def from_env(cls, env: Mapping[str, str] | None = None) -> "EmbeddingConfig":
    """Read embedding settings from `env` (defaulting to the process environment)."""
    env = os.environ if env is None else env
    raw_threshold = env.get("LIMITATION_MERGE_THRESHOLD")
    try:
      threshold = float(raw_threshold) if raw_threshold else DEFAULT_MERGE_THRESHOLD
    except ValueError as error:
      raise EmbeddingConfigError(
        f"LIMITATION_MERGE_THRESHOLD must be a number in (0, 1]; got {raw_threshold!r}."
      ) from error
    if not 0.0 < threshold <= 1.0:
      raise EmbeddingConfigError(
        f"LIMITATION_MERGE_THRESHOLD must be a number in (0, 1]; got {threshold}."
      )
    return cls(
      model_id=env.get("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL,
      revision=env.get("EMBEDDING_REVISION") or DEFAULT_EMBEDDING_REVISION,
      merge_threshold=threshold,
    )


class EmbeddingProvenance(BaseModel):
  """Which model produced the vectors behind a result, for its artifact."""

  model_id: str
  revision: str


class Embedder(Protocol):  # pylint: disable=too-few-public-methods
  """A boundary that turns texts into one vector each, in order."""

  model_id: str
  revision: str

  def embed(self, texts: Sequence[str]) -> list[Vector]:
    """Return one vector per text, in the order given."""
    raise NotImplementedError


class EmbeddingsUnavailableError(Exception):
  """Raised when the optional `embeddings` extra is not installed."""


class SentenceTransformerEmbedder:  # pylint: disable=too-few-public-methods
  """Embeds texts with a local `sentence-transformers` model (the default)."""

  def __init__(self, config: EmbeddingConfig):
    """Load the configured model at its pinned revision; fail if the extra is absent."""
    try:
      module: Any = importlib.import_module("sentence_transformers")
    except ImportError as error:
      raise EmbeddingsUnavailableError(
        "sentence-transformers is not installed; install the `embeddings` extra "
        "(uv sync --extra embeddings) to enable embedding-based merge hints."
      ) from error
    self.model_id = config.model_id
    self.revision = config.revision
    logger.info("Loading embedding model %s@%s", self.model_id, self.revision)
    self._model = module.SentenceTransformer(self.model_id, revision=self.revision)

  def embed(self, texts: Sequence[str]) -> list[Vector]:
    """Encode every text and return plain-float vectors."""
    if not texts:
      return []
    encoded = self._model.encode(list(texts), show_progress_bar=False)
    return [[float(value) for value in row] for row in encoded]


class RecordedEmbedder:  # pylint: disable=too-few-public-methods
  """A model-free embedder replaying recorded vectors by exact text."""

  def __init__(
    self,
    vectors: Mapping[str, Vector],
    *,
    model_id: str = DEFAULT_EMBEDDING_MODEL,
    revision: str = DEFAULT_EMBEDDING_REVISION,
  ):
    """Replay `vectors`, recorded from `model_id` at `revision`."""
    self._vectors = dict(vectors)
    self.model_id = model_id
    self.revision = revision

  def embed(self, texts: Sequence[str]) -> list[Vector]:
    """Return the recorded vector for each text, or fail if one is missing."""
    try:
      return [list(self._vectors[text]) for text in texts]
    except KeyError as error:
      raise LookupError(f"No recorded vector for text {error.args[0]!r}.") from error


def build_embedder(config: EmbeddingConfig | None = None) -> Embedder | None:
  """
  Return the default embedder, or None when the `embeddings` extra is absent.

  Absence is not an error: every stage that takes an embedder has a path
  without one (grouping runs LLM-only), so it is logged and skipped.
  """
  config = config or EmbeddingConfig.from_env()
  try:
    return SentenceTransformerEmbedder(config)
  except EmbeddingsUnavailableError as error:
    logger.info("Embeddings disabled: %s", error)
    return None
