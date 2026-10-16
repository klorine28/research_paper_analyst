"""Behavior of the LLM seam: structured outputs, disk caching, and offline fakes."""

import json
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
  LlmTruncatedError,
  LlmUsage,
  RecordedLlmClient,
  build_llm_client,
  complete_model,
  content_hash,
  request_fingerprint,
  write_usage,
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


def test_a_response_cut_off_at_max_tokens_is_a_truncation_error() -> None:
  """
  Output cut off by the token budget is reported, not returned as an empty object.

  Anthropic hands back a forced tool call cut off at `max_tokens` with an empty
  `input`; returned as-is it reads as "the model found no facts" (issue #124).
  """
  post = _RecordingPost(
    {
      "stop_reason": "max_tokens",
      "content": [{"type": "tool_use", "name": "emit", "input": {}}],
    }
  )
  client = AnthropicClient(LlmConfig(api_key="sk-test", max_tokens=4096), post=post)

  with pytest.raises(LlmTruncatedError, match="LLM_MAX_TOKENS"):
    client.complete("p", Extraction.model_json_schema(), prompt_version="v1")


def test_a_truncated_response_is_never_cached(tmp_path: Path) -> None:
  """A cut-off response raises before the cache stores it, so a rerun retries."""
  post = _RecordingPost(
    {"stop_reason": "max_tokens", "content": [{"type": "tool_use", "input": {}}]}
  )
  client = CachingLlmClient(
    AnthropicClient(LlmConfig(api_key="sk-test"), post=post), DiskCache(tmp_path)
  )

  with pytest.raises(LlmTruncatedError):
    client.complete("p", Extraction.model_json_schema(), prompt_version="v1")

  assert not list(tmp_path.glob("*.json"))


def test_the_default_output_budget_fits_a_full_extraction() -> None:
  """
  The default budget leaves room for a long Paper's Extraction.

  At 4096 tokens, 5 of the 50 `takotsubo-50` Papers had their Extraction cut
  off; the longest complete ones were already near 5000 tokens (issue #124).
  """
  assert LlmConfig.from_env({}).max_tokens >= 16384


class _OutcomeClient(_SequenceClient):  # pylint: disable=too-few-public-methods
  """Plays a scripted sequence of outcomes, raising those that are exceptions."""

  def __init__(self, outcomes: list[dict[str, Any] | Exception]):
    super().__init__([])
    self.outcomes = outcomes

  def complete(  # pylint: disable=unused-argument
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: str = "default",
    refresh: bool = False,
  ) -> dict[str, Any]:
    """Return or raise the next scripted outcome."""
    outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
    self.calls += 1
    if isinstance(outcome, Exception):
      raise outcome
    return outcome


def test_complete_model_retries_past_a_response_error() -> None:
  """A call that raises a response error is retried like a rejected result."""
  client = _OutcomeClient(
    [LlmTruncatedError("cut off"), {"research_question": "q", "findings": ["a"]}]
  )

  result = complete_model(client, "prompt", Extraction, prompt_version="v1", attempts=2)

  assert result.findings == ["a"]


def test_complete_model_raises_the_response_error_when_retries_run_out() -> None:
  """When every attempt raises, the last response error reaches the caller."""
  client = _OutcomeClient([LlmTruncatedError("cut off")])

  with pytest.raises(LlmTruncatedError):
    complete_model(client, "prompt", Extraction, prompt_version="v1", attempts=3)
  assert client.calls == 3


def test_a_response_error_after_an_empty_answer_is_what_gets_raised() -> None:
  """A cut-off last attempt says more than an earlier unaccepted, empty answer."""
  client = _OutcomeClient(
    [{"research_question": "", "findings": []}, LlmTruncatedError("cut off")]
  )

  with pytest.raises(LlmTruncatedError):
    complete_model(
      client,
      "prompt",
      Extraction,
      prompt_version="v1",
      attempts=2,
      accept=lambda ex: bool(ex.findings),
    )


def test_the_anthropic_client_tallies_token_usage_per_model() -> None:
  """Every billed call adds its input and output tokens to the usage tally."""
  response = _tool_use_response({"research_question": "q", "findings": []})
  response["usage"] = {"input_tokens": 1200, "output_tokens": 300}
  usage = LlmUsage()
  client = AnthropicClient(
    LlmConfig(api_key="sk-test", default_model="sonnet-x"),
    post=_RecordingPost(response),
    usage=usage,
  )

  client.complete("p", Extraction.model_json_schema(), prompt_version="v1")
  client.complete("q", Extraction.model_json_schema(), prompt_version="v1")

  assert usage.calls == 2
  assert usage.by_model["sonnet-x"].input_tokens == 2400
  assert usage.by_model["sonnet-x"].output_tokens == 600


def test_a_truncated_response_is_still_tallied() -> None:
  """Output cut off at the budget was still billed, so it still counts."""
  response = _tool_use_response({})
  response["stop_reason"] = "max_tokens"
  response["usage"] = {"input_tokens": 10, "output_tokens": 20}
  usage = LlmUsage()
  client = AnthropicClient(
    LlmConfig(api_key="sk-test"), post=_RecordingPost(response), usage=usage
  )

  with pytest.raises(LlmTruncatedError):
    client.complete("p", Extraction.model_json_schema(), prompt_version="v1")

  assert usage.calls == 1


def test_cache_hits_are_tallied_as_free_calls(tmp_path: Path) -> None:
  """A completion served from the cache counts as cached, not as a billed call."""
  usage = LlmUsage()
  inner = _CountingClient({"research_question": "q", "findings": []})
  client = CachingLlmClient(inner, DiskCache(tmp_path), usage=usage)
  schema = Extraction.model_json_schema()

  client.complete("prompt", schema, prompt_version="v1")
  client.complete("prompt", schema, prompt_version="v1")

  assert usage.cached_calls == 1


def test_usage_is_recorded_per_stage_keeping_other_stages(tmp_path: Path) -> None:
  """Recording one stage's usage replaces that stage's entry and keeps the rest."""
  path = tmp_path / "artifacts" / "llm-usage.json"
  first = LlmUsage(calls=1)
  write_usage(path, "extract", first)
  second = LlmUsage(cached_calls=4)
  write_usage(path, "detect", second)
  write_usage(path, "extract", LlmUsage(calls=7))

  recorded = json.loads(path.read_text(encoding="utf-8"))

  assert recorded["extract"]["calls"] == 7
  assert recorded["detect"]["cached_calls"] == 4
