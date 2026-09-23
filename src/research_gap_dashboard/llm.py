"""
The LLM boundary: one seam between the pipeline and the Anthropic API.

Every LLM call in the pipeline goes through `LlmClient.complete(prompt, schema)`,
which returns a schema-shaped dict via Anthropic structured outputs (ADR 0001).
The adapter owns every Anthropic wire detail; the rest of the pipeline only ever
sees plain dicts (or, through `complete_model`, validated pydantic models), so the
suite runs offline and keyless by injecting a fake transport or a
`RecordedLlmClient`.

Two model tiers are offered so a stage can trade cost for capability: the default
(a Sonnet-class model) and a cheap tier (a Haiku-class model). The concrete model
IDs live in configuration, not in code.

`CachingLlmClient` decorates any client with an on-disk cache keyed by
(content hash, prompt version) per ADR 0001: identical requests are free on
rerun, and bumping a prompt's version invalidates its entries. What deliberately
isn't here: prompt templates and their versions (each stage owns its own), and
JSON-Schema validation of arbitrary schemas (use `complete_model` to get pydantic
validation for free).
"""

import hashlib
import json
import logging
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Default model IDs; overridable from the environment (see .env.example). The
# cheap tier trades capability for cost on high-volume, low-stakes calls.
DEFAULT_MODEL = "claude-sonnet-4-5"
CHEAP_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 4096

# The single forced tool through which Anthropic emits structured output: its
# input schema is the caller's schema, so the tool input is the answer.
_STRUCTURED_TOOL_NAME = "emit_structured_output"

# Which model tier a call runs on. "default" is the capable model; "cheap" is
# the low-cost model for high-volume work.
ModelTier = Literal["default", "cheap"]

# A JSON POST: given a URL, headers, and a JSON body, return the parsed
# response. Clients depend on this, not on any HTTP library, so tests inject a
# fake transport and never touch the network.
JsonPost = Callable[[str, Mapping[str, str], dict[str, Any]], dict[str, Any]]

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class LlmConfigError(Exception):
  """Raised when required LLM configuration (e.g. the API key) is missing."""


class LlmResponseError(Exception):
  """Raised when a response carries no usable structured output."""


class LlmConfig(BaseModel):
  """LLM settings, read once from the environment and validated."""

  api_key: str | None = None
  default_model: str = DEFAULT_MODEL
  cheap_model: str = CHEAP_MODEL
  max_tokens: int = DEFAULT_MAX_TOKENS

  @classmethod
  def from_env(cls, env: Mapping[str, str] | None = None) -> "LlmConfig":
    """Read LLM settings from `env` (defaulting to the process environment)."""
    env = os.environ if env is None else env
    raw_max_tokens = env.get("LLM_MAX_TOKENS")
    return cls(
      api_key=env.get("ANTHROPIC_API_KEY") or None,
      default_model=env.get("LLM_DEFAULT_MODEL") or DEFAULT_MODEL,
      cheap_model=env.get("LLM_CHEAP_MODEL") or CHEAP_MODEL,
      max_tokens=int(raw_max_tokens) if raw_max_tokens else DEFAULT_MAX_TOKENS,
    )

  def model_for(self, tier: ModelTier) -> str:
    """Return the concrete model ID for a tier."""
    return self.cheap_model if tier == "cheap" else self.default_model


class LlmClient(Protocol):  # pylint: disable=too-few-public-methods
  """A boundary that turns a prompt plus an output schema into a dict."""

  name: str

  def complete(
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: ModelTier = "default",
  ) -> dict[str, Any]:
    """Return a dict shaped by `schema`, produced from `prompt`."""
    raise NotImplementedError


class AnthropicClient:  # pylint: disable=too-few-public-methods
  """Completes prompts with the Anthropic Messages API, the primary client."""

  name = "anthropic"

  def __init__(self, config: LlmConfig, post: JsonPost | None = None):
    """Wrap Anthropic; a missing API key is a run failure, not silent degradation."""
    if not config.api_key:
      raise LlmConfigError(
        "ANTHROPIC_API_KEY is required for the Anthropic client; set it in .env "
        "or use RecordedLlmClient for offline runs."
      )
    self._config = config
    self._api_key = config.api_key
    self._post = post or _httpx_post

  def complete(
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: ModelTier = "default",
  ) -> dict[str, Any]:
    """Force a single structured-output tool call and return its input dict."""
    model = self._config.model_for(tier)
    # Provenance: the model and prompt version behind every result (ADR 0001).
    logger.info(
      "LLM completion model=%s tier=%s prompt_version=%s", model, tier, prompt_version
    )
    payload: dict[str, Any] = {
      "model": model,
      "max_tokens": self._config.max_tokens,
      "tools": [
        {
          "name": _STRUCTURED_TOOL_NAME,
          "description": "Emit the answer as structured output matching the schema.",
          "input_schema": schema,
        }
      ],
      "tool_choice": {"type": "tool", "name": _STRUCTURED_TOOL_NAME},
      "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
      "x-api-key": self._api_key,
      "anthropic-version": ANTHROPIC_VERSION,
      "content-type": "application/json",
    }
    response = self._post(ANTHROPIC_MESSAGES_URL, headers, payload)
    return _structured_output(response)


class RecordedLlmClient:  # pylint: disable=too-few-public-methods
  """A keyless, offline client that replays responses by request fingerprint."""

  name = "recorded"

  def __init__(self, responses: Mapping[str, dict[str, Any]]):
    """Replay `responses`, keyed by `request_fingerprint`."""
    self._responses = dict(responses)

  @classmethod
  def from_file(cls, path: Path) -> "RecordedLlmClient":
    """Load recorded responses from a JSON file mapping fingerprints to dicts."""
    return cls(json.loads(path.read_text(encoding="utf-8")))

  def complete(
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: ModelTier = "default",
  ) -> dict[str, Any]:
    """Return the recorded response for this request, or fail if none exists."""
    fingerprint = request_fingerprint(prompt, schema, tier, prompt_version)
    try:
      return self._responses[fingerprint]
    except KeyError as error:
      raise LlmResponseError(
        f"No recorded LLM response for fingerprint {fingerprint}."
      ) from error


class CacheEntry(BaseModel):
  """One cached completion, with the provenance ADR 0001 requires."""

  content_hash: str
  prompt_version: str
  tier: ModelTier
  result: dict[str, Any]


class DiskCache:
  """An on-disk store of completions, one JSON file per request fingerprint."""

  def __init__(self, root: Path):
    """Store cache files under `root` (created on first write)."""
    self._root = root

  def get(self, fingerprint: str) -> dict[str, Any] | None:
    """Return a cached result for the fingerprint, or None on a miss."""
    path = self._path(fingerprint)
    if not path.exists():
      return None
    return CacheEntry.model_validate_json(path.read_text(encoding="utf-8")).result

  def put(self, fingerprint: str, entry: CacheEntry) -> None:
    """Write a completion to the cache under its fingerprint."""
    self._root.mkdir(parents=True, exist_ok=True)
    self._path(fingerprint).write_text(
      entry.model_dump_json(indent=2), encoding="utf-8"
    )

  def _path(self, fingerprint: str) -> Path:
    return self._root / f"{fingerprint}.json"


class CachingLlmClient:  # pylint: disable=too-few-public-methods
  """Decorates a client with a disk cache keyed by (content hash, prompt version)."""

  def __init__(self, inner: LlmClient, cache: DiskCache):
    """Serve `inner`'s completions from `cache`, populating it on a miss."""
    self._inner = inner
    self._cache = cache
    self.name = f"cached:{inner.name}"

  def complete(
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: ModelTier = "default",
  ) -> dict[str, Any]:
    """Return a cached completion when present, else call `inner` and cache it."""
    fingerprint = request_fingerprint(prompt, schema, tier, prompt_version)
    cached = self._cache.get(fingerprint)
    if cached is not None:
      logger.debug("LLM cache hit %s", fingerprint)
      return cached
    result = self._inner.complete(
      prompt, schema, prompt_version=prompt_version, tier=tier
    )
    self._cache.put(
      fingerprint,
      CacheEntry(
        content_hash=content_hash(prompt, schema, tier),
        prompt_version=prompt_version,
        tier=tier,
        result=result,
      ),
    )
    return result


def build_llm_client(
  config: LlmConfig | None = None, cache_dir: Path | None = None
) -> LlmClient:
  """Build the Anthropic client, wrapped in a disk cache when `cache_dir` is given."""
  config = config or LlmConfig.from_env()
  client: LlmClient = AnthropicClient(config)
  if cache_dir is not None:
    client = CachingLlmClient(client, DiskCache(cache_dir))
  return client


def complete_model(
  client: LlmClient,
  prompt: str,
  model: type[_ModelT],
  *,
  prompt_version: str,
  tier: ModelTier = "default",
) -> _ModelT:
  """Complete against a pydantic model's schema and validate the result into it."""
  result = client.complete(
    prompt,
    model.model_json_schema(),
    prompt_version=prompt_version,
    tier=tier,
  )
  return model.model_validate(result)


def content_hash(prompt: str, schema: dict[str, Any], tier: ModelTier) -> str:
  """Hash the request content: the prompt, the output schema, and the tier."""
  payload = json.dumps(
    {"prompt": prompt, "schema": schema, "tier": tier},
    sort_keys=True,
    ensure_ascii=False,
  )
  return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def request_fingerprint(
  prompt: str, schema: dict[str, Any], tier: ModelTier, prompt_version: str
) -> str:
  """Combine the content hash and prompt version into one cache/lookup key."""
  return f"{content_hash(prompt, schema, tier)}.{prompt_version}"


def _structured_output(response: dict[str, Any]) -> dict[str, Any]:
  """Pull the forced tool's input out of an Anthropic Messages response."""
  for block in response.get("content", []):
    if block.get("type") == "tool_use":
      return dict(block.get("input", {}))
  raise LlmResponseError("Anthropic response carried no tool_use block.")


def _httpx_post(
  url: str, headers: Mapping[str, str], payload: dict[str, Any]
) -> dict[str, Any]:
  """POST JSON to a URL and return the parsed response body."""
  response = httpx.post(url, headers=dict(headers), json=payload, timeout=120.0)
  response.raise_for_status()
  return response.json()
