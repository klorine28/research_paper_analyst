# 0002: Streamlit dashboard reading a framework-agnostic data artifact

## Status

Accepted

## Context

The dashboard needs interactivity (accept/reject gaps, paper comparison,
conversational analytics), which rules out a static HTML report. The
repository forbids npm/TypeScript tooling, keeping the project
single-language Python. The tool is intended to become open source, so every
tool in the stack must be free.

## Decision

The dashboard is built with Streamlit (Apache-2.0). The pipeline writes a
framework-agnostic data artifact (papers, extractions, gaps, evidence) to
disk; the dashboard only reads that artifact and never runs pipeline logic.

## Consequences

- The front end can be swapped (Dash, Shiny, static export) without touching
  the pipeline.
- All stack components are free and open source; the only paid dependency is
  the user's own Anthropic API key (ADR 0001).
- Streamlit's rerun-on-interaction model constrains how chat and accept/
  reject state are held (session state plus persisted files).
