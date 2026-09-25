"""Behavior of the LLM seam: structured outputs, disk caching, and offline fakes."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from research_gap_dashboard.llm import (
  AnthropicClient,
  CachingLlmClient,
  DiskCache,
  LlmConfig,
  LlmConfigError,
  LlmResponseError,
  RecordedLlmClient,
  build_llm_client,
  complete_model,
  content_hash,
  request_fingerprint,
)


class Extraction(BaseModel):
  """A tiny schema standing in for a real Extraction in these tests."""

  research_question: str
  findings: list[str]


def _tool_use_response(payload: dict[str, Any]) -> dict[str, Any]:
  """Shape a fake Anthropic Messages response that emits `payload` via a tool."""
  return {"content": [{"type": "tool_use", "name": "emit", "input": payload}]}


class _RecordingPost:  # pylint: disable=too-few-public-methods
  """A fake JSON transport that records calls and returns a canned response."""

  def __init__(self, response: dict[str, Any]):
    self.response = response
    self.calls: list[dict[str, Any]] = []

  def __call__(
    self, url: str, headers: Mapping[str, str], payload: dict[str, Any]
  ) -> dict[str, Any]:
    """Record the request and return the canned response."""
    self.calls.append({"url": url, "headers": headers, "payload": payload})
    return self.response


class _CountingClient:  # pylint: disable=too-few-public-methods
  """A fake LlmClient that counts calls, for exercising the cache."""

  name = "counting"

  def __init__(self, result: dict[str, Any]):
    self.result = result
    self.calls = 0

  def complete(  # pylint: disable=unused-argument
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: str = "default",
    refresh: bool = False,
  ) -> dict[str, Any]:
    """Count the call and return the fixed result."""
    self.calls += 1
    return self.result


def test_structured_call_returns_schema_valid_dict() -> None:
  """A forced structured-output call returns a dict matching the asked schema."""
  answer = {"research_question": "Does X cause Y?", "findings": ["a", "b"]}
  post = _RecordingPost(_tool_use_response(answer))
  client = AnthropicClient(LlmConfig(api_key="sk-test"), post=post)

  result = client.complete(
    "Extract facts.", Extraction.model_json_schema(), prompt_version="v1"
  )

  assert result == answer
  # The result matches the schema it was asked for.
  Extraction.model_validate(result)


def test_anthropic_client_forces_the_structured_tool() -> None:
  """The client forces the emit tool and sends the schema and API key."""
  post = _RecordingPost(_tool_use_response({"research_question": "q", "findings": []}))
  client = AnthropicClient(LlmConfig(api_key="sk-test"), post=post)

  client.complete("prompt", Extraction.model_json_schema(), prompt_version="v1")

  payload = post.calls[0]["payload"]
  assert payload["tool_choice"]["type"] == "tool"
  assert payload["tools"][0]["input_schema"] == Extraction.model_json_schema()
  assert post.calls[0]["headers"]["x-api-key"] == "sk-test"


def test_tier_selects_the_cheap_model() -> None:
  """The cheap tier routes the request to the configured cheap model."""
  post = _RecordingPost(_tool_use_response({"research_question": "q", "findings": []}))
  config = LlmConfig(api_key="sk-test", default_model="sonnet-x", cheap_model="haiku-x")
  client = AnthropicClient(config, post=post)

  client.complete(
    "p", Extraction.model_json_schema(), prompt_version="v1", tier="cheap"
  )

  assert post.calls[0]["payload"]["model"] == "haiku-x"


def test_missing_tool_use_raises() -> None:
  """A response without a tool_use block is a usable-output failure."""
  post = _RecordingPost({"content": [{"type": "text", "text": "no tool here"}]})
  client = AnthropicClient(LlmConfig(api_key="sk-test"), post=post)

  with pytest.raises(LlmResponseError):
    client.complete("p", Extraction.model_json_schema(), prompt_version="v1")


def test_complete_model_validates_into_the_model() -> None:
  """complete_model returns the result parsed into the pydantic model."""
  answer = {"research_question": "Does X cause Y?", "findings": ["a"]}
  client = AnthropicClient(
    LlmConfig(api_key="sk-test"), post=_RecordingPost(_tool_use_response(answer))
  )

  extraction = complete_model(client, "prompt", Extraction, prompt_version="v1")

  assert isinstance(extraction, Extraction)
  assert extraction.findings == ["a"]


def test_complete_model_unwraps_a_spurious_wrapper_key() -> None:
  """Fields wrapped under a junk key (a real Anthropic quirk) are recovered."""
  real = {"research_question": "Does X cause Y?", "findings": ["a", "b"]}
  for wrapper in ("parameters", "$PARAMETER_NAME", "$STRUCTURED_OUTPUT", "$schema"):
    client = AnthropicClient(
      LlmConfig(api_key="sk-test"),
      post=_RecordingPost(_tool_use_response({wrapper: real})),
    )

    extraction = complete_model(client, "prompt", Extraction, prompt_version="v1")

    assert extraction.findings == ["a", "b"], wrapper


def test_complete_model_drops_schema_metadata_beside_real_fields() -> None:
  """A stray $defs alongside the real fields is ignored, not treated as data."""
  payload = {
    "$defs": {"Whatever": {"type": "object"}},
    "research_question": "q",
    "findings": ["a"],
  }
  client = AnthropicClient(
    LlmConfig(api_key="sk-test"), post=_RecordingPost(_tool_use_response(payload))
  )

  extraction = complete_model(client, "prompt", Extraction, prompt_version="v1")

  assert extraction.findings == ["a"]


class _SequenceClient:  # pylint: disable=too-few-public-methods
  """Returns a scripted sequence of responses, counting refresh calls."""

  name = "sequence"

  def __init__(self, results: list[dict[str, Any]]):
    self.results = results
    self.calls = 0

  def complete(  # pylint: disable=unused-argument
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: str = "default",
    refresh: bool = False,
  ) -> dict[str, Any]:
    """Return the next scripted response."""
    result = self.results[min(self.calls, len(self.results) - 1)]
    self.calls += 1
    return result


def test_complete_model_retries_past_a_rejected_result() -> None:
  """An `accept`-rejected result is retried until one is accepted."""
  empty = {"research_question": "", "findings": []}
  good = {"research_question": "q", "findings": ["a"]}
  client = _SequenceClient([empty, good])

  result = complete_model(
    client,
    "prompt",
    Extraction,
    prompt_version="v1",
    attempts=3,
    accept=lambda ex: bool(ex.findings),
  )

  assert result.findings == ["a"]
  assert client.calls == 2


def test_complete_model_retry_bypasses_the_cache(tmp_path: Path) -> None:
  """Retries refresh the cache so a bad first response is not served again."""
  inner = _SequenceClient(
    [
      {"research_question": "", "findings": []},
      {"research_question": "q", "findings": ["a"]},
    ]
  )
  client = CachingLlmClient(inner, DiskCache(tmp_path))

  result = complete_model(
    client,
    "prompt",
    Extraction,
    prompt_version="v1",
    attempts=3,
    accept=lambda ex: bool(ex.findings),
  )

  assert result.findings == ["a"]
  assert inner.calls == 2


def test_identical_requests_hit_the_disk_cache(tmp_path: Path) -> None:
  """A repeated request is served from disk and never reaches the inner client."""
  inner = _CountingClient({"research_question": "q", "findings": []})
  client = CachingLlmClient(inner, DiskCache(tmp_path))
  schema = Extraction.model_json_schema()

  first = client.complete("prompt", schema, prompt_version="v1")
  second = client.complete("prompt", schema, prompt_version="v1")

  assert first == second
  assert inner.calls == 1
  cache_files = list(tmp_path.glob("*.json"))
  assert len(cache_files) == 1


def test_prompt_version_change_invalidates_the_cache(tmp_path: Path) -> None:
  """Bumping the prompt version misses the cache and calls the inner client again."""
  inner = _CountingClient({"research_question": "q", "findings": []})
  client = CachingLlmClient(inner, DiskCache(tmp_path))
  schema = Extraction.model_json_schema()

  client.complete("prompt", schema, prompt_version="v1")
  client.complete("prompt", schema, prompt_version="v2")

  assert inner.calls == 2
  assert len(list(tmp_path.glob("*.json"))) == 2


def test_cache_entry_records_provenance(tmp_path: Path) -> None:
  """Each cache entry records the prompt version and tier behind the result."""
  inner = _CountingClient({"research_question": "q", "findings": []})
  client = CachingLlmClient(inner, DiskCache(tmp_path))
  schema = Extraction.model_json_schema()

  client.complete("prompt", schema, prompt_version="v1", tier="cheap")

  entry = list(tmp_path.glob("*.json"))[0].read_text(encoding="utf-8")
  assert '"prompt_version": "v1"' in entry
  assert '"tier": "cheap"' in entry


def test_fingerprint_changes_with_content_and_version() -> None:
  """The fingerprint varies with prompt, tier, and prompt version."""
  schema = Extraction.model_json_schema()
  base = request_fingerprint("p", schema, "default", "v1")

  assert request_fingerprint("p2", schema, "default", "v1") != base
  assert request_fingerprint("p", schema, "cheap", "v1") != base
  assert request_fingerprint("p", schema, "default", "v2") != base
  assert content_hash("p", schema, "default") in base


def test_recorded_client_replays_offline_without_a_key() -> None:
  """The recorded client replays a canned response with no API key."""
  schema = Extraction.model_json_schema()
  answer = {"research_question": "q", "findings": ["x"]}
  fingerprint = request_fingerprint("prompt", schema, "default", "v1")
  client = RecordedLlmClient({fingerprint: answer})

  assert client.complete("prompt", schema, prompt_version="v1") == answer


def test_recorded_client_fails_on_unknown_request() -> None:
  """An unrecorded request is a usable-output failure, not a silent default."""
  client = RecordedLlmClient({})

  with pytest.raises(LlmResponseError):
    client.complete("prompt", Extraction.model_json_schema(), prompt_version="v1")


def test_recorded_client_from_file(tmp_path: Path) -> None:
  """Recorded responses load from a JSON file keyed by fingerprint."""
  schema = Extraction.model_json_schema()
  answer = {"research_question": "q", "findings": []}
  fingerprint = request_fingerprint("prompt", schema, "default", "v1")
  path = tmp_path / "recorded.json"
  path.write_text('{"%s": %s}' % (fingerprint, __import__("json").dumps(answer)))

  client = RecordedLlmClient.from_file(path)

  assert client.complete("prompt", schema, prompt_version="v1") == answer


def test_missing_api_key_is_a_run_failure() -> None:
  """Constructing the Anthropic client without a key fails loudly."""
  with pytest.raises(LlmConfigError):
    AnthropicClient(LlmConfig(api_key=None))


def test_config_from_env_defaults_and_overrides() -> None:
  """Config falls back to model defaults and honors environment overrides."""
  defaults = LlmConfig.from_env({})
  assert defaults.api_key is None
  assert defaults.model_for("default") == "claude-sonnet-4-5"
  assert defaults.model_for("cheap") == "claude-haiku-4-5"

  overridden = LlmConfig.from_env(
    {
      "ANTHROPIC_API_KEY": "sk-env",
      "LLM_DEFAULT_MODEL": "sonnet-z",
      "LLM_CHEAP_MODEL": "haiku-z",
      "LLM_MAX_TOKENS": "1234",
    }
  )
  assert overridden.api_key == "sk-env"
  assert overridden.model_for("default") == "sonnet-z"
  assert overridden.model_for("cheap") == "haiku-z"
  assert overridden.max_tokens == 1234


def test_build_llm_client_wraps_in_cache_when_dir_given(tmp_path: Path) -> None:
  """The factory wraps the client in a disk cache only when a dir is given."""
  cached = build_llm_client(LlmConfig(api_key="sk-test"), cache_dir=tmp_path)
  assert isinstance(cached, CachingLlmClient)

  uncached = build_llm_client(LlmConfig(api_key="sk-test"))
  assert isinstance(uncached, AnthropicClient)
