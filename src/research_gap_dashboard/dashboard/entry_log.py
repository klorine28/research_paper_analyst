"""
The Corpus entry log: how and when each Paper entered the Corpus (ADR 0010 §4).

A Corpus grown by following the tool's own suggestions is not the same as a
searched one, so the route and date of every Paper are part of its scope. The
log sits at the Corpus root as `entry_log.json`, next to `scope.json`: like the
scope it is a Corpus input the dashboard writes (ADR 0007), not an artifact.

The log holds two lists:

- **entries**: one per Paper, written when it is placed (a dashboard upload,
  or a suggestion added through the pipeline's Grow path), keyed by citation
  key and/or DOI so it can be matched to the manifest Paper ingest makes of it;
- **unavailable**: suggested papers the researcher could not obtain (ADR 0009),
  which are never Papers but whose absence is disclosed with the scope. The
  log is the one record of them: the Grow path's `suggested_papers.json` keeps
  only what it still has to fetch. Papers that older Corpora marked
  unavailable there are carried over by `adopt_legacy_unavailable`.

A manifest Paper the log has never seen (every Paper of a Corpus built before
the log existed, or one added in a terminal) is recorded as "imported", dated
when the manifest that first listed it was written; `reconcile_entry_log` makes
that permanent so the date does not move with later ingests.

Plain functions with no Streamlit dependency, importing no pipeline stage
(ADR 0002).
"""

import logging
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime, timezone
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  ARTIFACTS_DIR,
  ENTRY_LOG_NAME,
  MANIFEST_NAME,
  SUGGESTED_PAPERS_NAME,
  ManifestArtifact,
)

logger = logging.getLogger(__name__)


class EntryRoute(StrEnum):
  """How a Paper entered the Corpus, in the order summaries list them."""

  IMPORTED = "imported"
  UPLOADED = "uploaded"
  RETRIEVAL_SUGGESTION = "retrieval_suggestion"
  SATURATION_HIT = "saturation_hit"


# The Grow path's names for its two routes (`grow.Route`), restated here.
_SUGGESTION_ROUTES = {
  "retrieval": EntryRoute.RETRIEVAL_SUGGESTION,
  "saturation": EntryRoute.SATURATION_HIT,
}


def suggestion_route(route: str) -> EntryRoute:
  """Map a Grow-path route ("retrieval" or "saturation") to its entry route."""
  return _SUGGESTION_ROUTES[route]


class LogEntry(BaseModel):
  """One Paper's entry into the Corpus. At least one of key or DOI is set."""

  citation_key: str = ""
  doi: str = ""
  route: EntryRoute
  entered_at: datetime


class UnavailablePaper(BaseModel):
  """A suggested paper the researcher could not obtain, kept out of the Corpus."""

  doi: str = ""
  pmid: str = ""
  openalex_id: str = ""
  title: str = ""
  route: EntryRoute
  marked_at: datetime
  reason: str = ""

  def ids(self) -> set[str]:
    """Return comparable ids, so any shared DOI, PMID or OpenAlex id matches."""
    return work_ids(self.doi, self.pmid, self.openalex_id)


class EntryLog(BaseModel):
  """The on-disk entry log of one Corpus."""

  entries: list[LogEntry] = []
  unavailable: list[UnavailablePaper] = []


class PaperIdentity(BaseModel):
  """
  What identifies a Paper to the log: its citation key, its DOI, or both.

  A PMID or OpenAlex id is not kept in the entry, but clears a matching
  unavailable record when the Paper enters after all.
  """

  citation_key: str = ""
  doi: str = ""
  pmid: str = ""
  openalex_id: str = ""


class PaperEntry(BaseModel):
  """One Corpus Paper with its route and date, as the Overview lists it."""

  citation_key: str
  label: str
  route: EntryRoute
  entered_at: datetime


class RouteCount(BaseModel):
  """How many Corpus Papers entered by one route."""

  route: EntryRoute
  count: int

  @property
  def label(self) -> str:
    """The count as a reader says it, e.g. "2 from retrieval suggestions"."""
    return text.ENTRY_ROUTE_SUMMARY[self.route.value].format(count=self.count)


class TimelineStep(BaseModel):
  """The Papers that entered on one day by one route, and the Corpus size after."""

  day: date
  route: EntryRoute
  count: int
  corpus_size: int


class EntryLogView(BaseModel):
  """The entry log joined to the manifest: what the Overview and scope show."""

  papers: list[PaperEntry]
  unavailable: list[UnavailablePaper]

  @property
  def route_counts(self) -> list[RouteCount]:
    """Papers per route, in route order, leaving out routes nobody took."""
    counts = Counter(paper.route for paper in self.papers)
    return [
      RouteCount(route=route, count=counts[route])
      for route in EntryRoute
      if counts[route]
    ]

  @property
  def summary(self) -> str:
    """One line, e.g. "15 imported · 2 from retrieval suggestions"."""
    return " \u00b7 ".join(count.label for count in self.route_counts)

  @property
  def grown_beyond_import(self) -> bool:
    """Whether any Paper entered after the initial import, by any other route."""
    return any(paper.route != EntryRoute.IMPORTED for paper in self.papers)

  @property
  def timeline(self) -> list[TimelineStep]:
    """How the Corpus grew: Papers per day and route, oldest first."""
    counts = Counter((paper.entered_at.date(), paper.route) for paper in self.papers)
    route_order = list(EntryRoute)
    steps: list[TimelineStep] = []
    size = 0
    for (day, route), count in sorted(
      counts.items(), key=lambda item: (item[0][0], route_order.index(item[0][1]))
    ):
      size += count
      steps.append(TimelineStep(day=day, route=route, count=count, corpus_size=size))
    return steps


def entry_log_path(corpus_root: Path) -> Path:
  """Return where a Corpus keeps its entry log."""
  return corpus_root / ENTRY_LOG_NAME


def load_entry_log(corpus_root: Path) -> EntryLog:
  """Read a Corpus's entry log, or an empty one when nothing was recorded yet."""
  path = entry_log_path(corpus_root)
  if not path.is_file():
    return EntryLog()
  return EntryLog.model_validate_json(path.read_text(encoding="utf-8"))


def _save(corpus_root: Path, log: EntryLog) -> None:
  entry_log_path(corpus_root).write_text(
    log.model_dump_json(indent=2), encoding="utf-8"
  )


def normalize_doi(doi: str) -> str:
  """Return a DOI in the one form the log compares: bare and lower-case."""
  value = doi.strip().lower()
  for prefix in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "doi:"):
    if value.startswith(prefix):
      return value[len(prefix) :]
  return value


def work_ids(doi: str, pmid: str = "", openalex_id: str = "") -> set[str]:
  """Return comparable `doi:`, `pmid:` and `openalex:` ids for one work."""
  ids: set[str] = set()
  if doi.strip():
    ids.add("doi:" + normalize_doi(doi))
  if pmid.strip():
    ids.add(f"pmid:{pmid.strip()}")
  if openalex_id.strip():
    ids.add("openalex:" + openalex_id.strip().rsplit("/", 1)[-1].upper())
  return ids


def _find(log: EntryLog, citation_key: str, doi: str) -> LogEntry | None:
  """Return the log entry for a Paper, matched by citation key or DOI."""
  doi = normalize_doi(doi)
  for entry in log.entries:
    if citation_key and entry.citation_key == citation_key:
      return entry
    if doi and entry.doi == doi:
      return entry
  return None


def _now() -> datetime:
  return datetime.now(timezone.utc)


def record_entries(
  corpus_root: Path,
  papers: Iterable[PaperIdentity],
  route: EntryRoute,
  *,
  at: datetime | None = None,
) -> int:
  """
  Log that these Papers entered the Corpus by `route`, returning how many were new.

  A Paper already in the log keeps its first route and date: entering is a
  one-time event, and re-uploading a list must not rewrite history. Recording a
  Paper also clears it from the unavailable list, since it was obtained after all.
  """
  log = load_entry_log(corpus_root)
  when = at or _now()
  added = 0
  for paper in papers:
    key, doi = paper.citation_key.strip(), normalize_doi(paper.doi)
    if not key and not doi:
      continue
    if _find(log, key, doi) is not None:
      continue
    log.entries.append(
      LogEntry(citation_key=key, doi=doi, route=route, entered_at=when)
    )
    ids = work_ids(doi, paper.pmid, paper.openalex_id)
    if ids:
      log.unavailable = [item for item in log.unavailable if not item.ids() & ids]
    added += 1
  if added:
    _save(corpus_root, log)
    logger.info("Logged %d Paper(s) entering %s as %s", added, corpus_root, route)
  return added


def mark_unavailable(  # pylint: disable=too-many-arguments
  corpus_root: Path,
  *,
  doi: str,
  title: str,
  route: EntryRoute,
  reason: str = "",
  pmid: str = "",
  openalex_id: str = "",
  at: datetime | None = None,
) -> UnavailablePaper:
  """
  Record a suggested paper as unavailable, so its absence is disclosed.

  Marking the same work again (any shared DOI, PMID or OpenAlex id) replaces
  the earlier record rather than repeating it.
  """
  log = load_entry_log(corpus_root)
  record = UnavailablePaper(
    doi=normalize_doi(doi),
    pmid=pmid.strip(),
    openalex_id=openalex_id.strip(),
    title=title.strip(),
    route=route,
    marked_at=at or _now(),
    reason=reason.strip(),
  )
  ids = record.ids()
  log.unavailable = [item for item in log.unavailable if not item.ids() & ids]
  log.unavailable.append(record)
  _save(corpus_root, log)
  logger.info("Marked %s unavailable in %s", record.doi or record.title, corpus_root)
  return record


class _LegacySuggestion(BaseModel):
  model_config = ConfigDict(extra="ignore")

  doi: str = ""
  pmid: str = ""
  openalex_id: str = ""
  title: str = ""
  routes: list[str] = []
  status: str = ""
  reason: str = ""
  updated_at: datetime


class _LegacySuggestions(BaseModel):
  model_config = ConfigDict(extra="ignore")

  papers: list[_LegacySuggestion] = []


def adopt_legacy_unavailable(corpus_root: Path) -> int:
  """
  Carry papers marked unavailable in `suggested_papers.json` into the log.

  Before the log owned "unavailable", the Grow path kept that status in its own
  record. Each such paper the log does not know yet (neither as unavailable
  nor as an entered Paper) is recorded with its original date and reason, so
  it is not lost. Returns how many were carried over; the suggested-Papers
  file itself is left alone (it is the pipeline's to rewrite).
  """
  path = corpus_root / SUGGESTED_PAPERS_NAME
  if not path.is_file():
    return 0
  try:
    legacy = _LegacySuggestions.model_validate_json(path.read_text(encoding="utf-8"))
  except ValidationError:
    logger.error("Could not read %s; no unavailable papers carried over", path)
    return 0
  log = load_entry_log(corpus_root)
  known = {work_id for item in log.unavailable for work_id in item.ids()}
  known |= {f"doi:{entry.doi}" for entry in log.entries if entry.doi}
  adopted = 0
  for paper in legacy.papers:
    ids = work_ids(paper.doi, paper.pmid, paper.openalex_id)
    if paper.status != "unavailable" or not ids or ids & known:
      continue
    route = next(
      (_SUGGESTION_ROUTES[r] for r in paper.routes if r in _SUGGESTION_ROUTES),
      EntryRoute.RETRIEVAL_SUGGESTION,
    )
    log.unavailable.append(
      UnavailablePaper(
        doi=normalize_doi(paper.doi),
        pmid=paper.pmid.strip(),
        openalex_id=paper.openalex_id.strip(),
        title=paper.title.strip(),
        route=route,
        marked_at=paper.updated_at,
        reason=paper.reason.strip(),
      )
    )
    known |= ids
    adopted += 1
  if adopted:
    _save(corpus_root, log)
    logger.info("Carried %d unavailable paper(s) into %s", adopted, corpus_root)
  return adopted


def _manifest_written_at(corpus_root: Path) -> datetime:
  """When the manifest was last written: the best date for an unlogged Paper."""
  path = corpus_root / ARTIFACTS_DIR / MANIFEST_NAME
  if not path.is_file():
    return _now()
  return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def reconcile_entry_log(corpus_root: Path, manifest: ManifestArtifact) -> EntryLogView:
  """
  Give every manifest Paper a logged route and date, then return the joined view.

  Papers the log has never seen are written as "imported", dated by the
  manifest, so an existing Corpus gets a complete log the first time it is
  opened. Log entries for papers that are not (or no longer) in the manifest
  stay on disk but are not shown: they never became Papers. Papers an older
  Corpus marked unavailable outside the log are carried into it first.
  """
  adopt_legacy_unavailable(corpus_root)
  log = load_entry_log(corpus_root)
  unlogged = [
    PaperIdentity(citation_key=paper.citation_key, doi=paper.doi)
    for paper in manifest.papers
    if _find(log, paper.citation_key, paper.doi) is None
  ]
  if unlogged:
    record_entries(
      corpus_root,
      unlogged,
      EntryRoute.IMPORTED,
      at=_manifest_written_at(corpus_root),
    )
  return build_entry_log_view(load_entry_log(corpus_root), manifest)


def build_entry_log_view(log: EntryLog, manifest: ManifestArtifact) -> EntryLogView:
  """
  Join the log to the manifest: each Paper with its route and date, oldest first.

  A Paper with no log entry is shown as "imported" at the earliest logged date
  (or now, with an empty log); `reconcile_entry_log` is what records it.
  An unavailable paper that is in the manifest after all is not disclosed as
  missing.
  """
  fallback = min((entry.entered_at for entry in log.entries), default=_now())
  papers: list[PaperEntry] = []
  corpus_dois: set[str] = set()
  for paper in manifest.papers:
    entry = _find(log, paper.citation_key, paper.doi)
    corpus_dois.add(normalize_doi(paper.doi))
    papers.append(
      PaperEntry(
        citation_key=paper.citation_key,
        label=paper.title or paper.citation_key,
        route=entry.route if entry is not None else EntryRoute.IMPORTED,
        entered_at=entry.entered_at if entry is not None else fallback,
      )
    )
  papers.sort(key=lambda paper: (paper.entered_at, paper.label))
  corpus_dois.discard("")
  unavailable = sorted(
    (item for item in log.unavailable if item.doi not in corpus_dois),
    key=lambda item: item.marked_at,
  )
  return EntryLogView(papers=papers, unavailable=unavailable)
