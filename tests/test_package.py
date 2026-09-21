"""Smoke test so the suite runs green before real features land."""

import research_gap_dashboard


def test_package_imports():
  """The package is installed and importable."""
  assert research_gap_dashboard.__doc__
