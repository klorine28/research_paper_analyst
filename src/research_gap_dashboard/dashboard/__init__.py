"""
The dashboard: a thin front end that only reads on-disk artifacts.

Per ADR 0002 the dashboard reads the framework-agnostic artifacts the pipeline
writes and never runs pipeline logic. Nothing in this package imports a
pipeline stage module; the artifact schema the dashboard depends on lives here
in its own read models, so the front end could be swapped without touching the
pipeline.
"""
