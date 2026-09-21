# 0001: LLM-based extraction using the Anthropic API

## Status

Accepted

## Context

Extraction (stage 2 of the pipeline) must pull structured facts from
full-text PDFs across heterogeneous paper formats. Rule/NLP-based extraction
(regex, spaCy pipelines, GROBID-only) handles section splitting well but not
semantic fields like "stated limitations" or "key findings". The corpus is
small (10–75 papers), so per-paper LLM calls are affordable.

## Decision

Extraction is LLM-based, using the Anthropic API. PDF parsing to sections is
done locally with open-source tooling; the LLM converts section text into the
structured Extraction. LLM responses are cached on disk keyed by
(paper content hash, prompt version) so reruns are free and repeatable.

The specific model and PDF-parsing library are chosen by a separate research
task and may change without revisiting this ADR.

## Consequences

- Extraction quality depends on prompt design and must be spot-checked
  against source papers (no invented facts; every fact links to Evidence).
- Running the pipeline requires an Anthropic API key; the tool is
  open-source, so users bring their own key (see ADR 0002 context).
- The cache makes stage reruns cheap but must be invalidated when prompts
  change (hence the prompt-version key component).
