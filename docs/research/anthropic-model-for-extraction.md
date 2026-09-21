# Research: Which Anthropic model for Extraction?

Question from the grilling session (ADR 0001 left this open): which Anthropic
model should power the Extraction stage (structured facts from full-text
sections of 10–75 papers), and which API features apply?

Sources: Anthropic's official docs, fetched 2025 from
`docs.claude.com` (Models overview, Structured outputs, PDF support pages).

## Current model lineup and pricing

Per the [Models overview](https://docs.claude.com/en/docs/about-claude/models/overview):

| Model | API ID | Input / Output per MTok | Context | Notes |
| --- | --- | --- | --- | --- |
| Claude Fable 5.1 | `claude-fable-5-1` | $10 / $50 | 1M | Demanding long-horizon reasoning |
| Claude Opus 5 | `claude-opus-5` | $5 / $25 | 1M | Complex agentic work |
| Claude Sonnet 5 | `claude-sonnet-5` | $2 / $10 | 1M | Best speed/intelligence balance |
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001` | $1 / $5 | 200K | Fastest, near-frontier |

## Relevant API features

- **Structured outputs** (`output_config.format` with `type: "json_schema"`)
  guarantee the response is valid JSON matching a schema — exactly what the
  Extraction record needs. Supported on Sonnet 5, Haiku 4.5, Opus 5, and
  Fable 5/5.1 (per the Structured outputs page, "Supported models"). The old
  `output_format` beta parameter is deprecated; use `output_config`.
- **Native PDF support**: up to 32 MB per request and 600 pages (100 pages
  when the context window in use is under 1M tokens); Files API recommended
  for large PDFs. Claude reads text *and* figures/tables visually.
- **Citations API**: Claude can return passage-level citations into provided
  documents — directly useful for the Evidence requirement (every extracted
  fact links to a passage).

## Recommendation

- **Default extraction model: Claude Sonnet 5** (`claude-sonnet-5`) with
  structured outputs. Strong enough for semantic fields (limitations, future
  work), 5× cheaper than Opus 5.
- **Cost check**: a full-text paper is roughly 10–20K tokens; 75 papers ≈
  ~1.1M input tokens ≈ **~$2–4 per corpus run** on Sonnet 5 (output adds
  little). Even Opus 5 stays under ~$10, so the cache (ADR 0001) matters more
  for repeatability than cost.
- **Cheap tier option**: expose Haiku 4.5 as a config option for
  cost-sensitive users; expect weaker limitation/future-work extraction.
- **Design note**: Anthropic's native PDF + citations support means the LLM
  could ingest PDFs directly (skipping local section parsing) and return
  cited passages. Recommended hybrid: parse locally for sectioning and
  offline artifacts (see `pdf-parsing-library.md`), but consider the
  Citations API for Evidence anchoring in a later iteration.
- Pin the model ID in config (not code) and record it in the extraction cache
  key alongside the prompt version.
