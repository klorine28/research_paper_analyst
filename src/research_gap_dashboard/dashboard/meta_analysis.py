"""
Assembling the field meta-analysis layer: six honest graphs of the Corpus.

These are plain functions with no Streamlit dependency (ADR 0002): the layout
layer asks here for a finished graph model and a finished Plotly figure. The
graph model is built with networkx; the figure is rendered with Plotly, reusing
the dashboard's existing Plotly idiom (issue #49).

Every graph carries, as data, the question it answers and the incomplete-Corpus
caveat it inherits, so the honesty controls travel with the chart rather than
living only in the layout code (CODING_STANDARDS.md > Research integrity). Each
graph also distinguishes *absent* structure from *measured* structure: an edge
that is missing means "no Paper in the Corpus links these two", a fact about the
Corpus, never a value of zero. Missing edges and isolated nodes are surfaced,
not hidden, because they are where the gaps live.

The graphs, and the gap lens each one offers:

- Citation network: in-Corpus citation edges from ``referenced_works``; an
  isolated node is a Retrieval-Gap signal.
- Topic co-occurrence: Topics that appear together in a Paper; a missing edge is
  a Knowledge-Gap signal.
- Method co-occurrence: Methods that appear together in a Paper; a missing edge
  is a Coverage-Gap signal.
- Author collaboration: co-authorship between Corpus Papers; labelled
  field-meta, explicitly not a gap.
- Limitation follow-up: which stated limitations a later Paper addressed; an
  unaddressed limitation is an Unanswered-Limitation signal.

Publication trends over time are the sixth graph in the meta-analysis set; they
are already built by ``dashboard.coverage`` and reused by the layout, so they
are not rebuilt here.
"""

from itertools import combinations
from typing import Literal

import networkx as nx
import plotly.graph_objects as go
from pydantic import BaseModel

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  ManifestArtifact,
  NormalizedFactsArtifact,
  PaperRecord,
)
from research_gap_dashboard.dashboard.limitations import (
  LimitationGroupView,
  LimitationsData,
)

# The layout seed is fixed so the same Corpus always draws the same graph: a
# reproducible figure is an honesty requirement, not a convenience.
_LAYOUT_SEED = 42

# Co-occurrence graphs cap their node count so a large taxonomy axis never buries
# the structure; the most-covered categories are kept and the rest reported as a
# note so the denominator still travels with the chart.
_MAX_COOCCURRENCE_NODES = 25

# Node labels longer than this are trimmed on the graph (full text stays in the
# hover), so long taxonomy labels do not overlap into an unreadable tangle.
_MAX_LABEL_CHARS = 26

# Below this many nodes an axis reads as "thin": too few categories mapped for a
# co-occurrence graph to say much, usually an upstream taxonomy-coverage matter.
_THIN_AXIS_NODES = 3

# The default number of ranked missing pairs the layout shows before "+N more".
MISSING_PAIRS_SHOWN = 15

# How many authors the collaboration table lists by default.
AUTHOR_TABLE_ROWS = 20

# Human labels for the co-occurrence axes, kept here (not in detect: ADR 0002).
_AXIS_LABELS: dict[str, str] = {"topic": "Topic", "method": "Method"}

GraphKind = Literal["citation", "cooccurrence"]


class GraphNode(BaseModel):
  """One node in a network graph: its identity, a size metric, and hover text."""

  node_id: str
  label: str
  weight: int = 1
  hover: str = ""
  isolated: bool = False


class GraphEdge(BaseModel):
  """One undirected or directed edge, weighted by how many Papers back it."""

  source: str
  target: str
  weight: int = 1
  hover: str = ""


class MissingPair(BaseModel):
  """A category pair no Paper in the Corpus combines: a candidate gap, as text."""

  source_id: str
  target_id: str
  source_label: str
  target_label: str
  strength: int = 0  # min Papers on either endpoint: how strong the gap signal is


class NetworkGraph(BaseModel):
  """
  One network graph, built by a builder and handed to the figure renderer.

  ``question`` and ``caveat`` are carried as data so every chart states what it
  answers and inherits the incomplete-Corpus caveat. ``gap_lens`` names the Gap
  Type the graph's absent structure points at. ``notes`` hold denominators and
  capping, ``missing_pairs`` the actionable absent edges (co-occurrence only).
  """

  kind: GraphKind
  slug: str
  title: str
  question: str
  caveat: str
  gap_lens: str
  directed: bool = False
  nodes: list[GraphNode] = []
  edges: list[GraphEdge] = []
  missing_pairs: list[MissingPair] = []
  notes: list[str] = []

  @property
  def is_empty(self) -> bool:
    """Whether the graph has any node to draw."""
    return not self.nodes

  @property
  def edge_count(self) -> int:
    """How many edges the graph holds."""
    return len(self.edges)

  @property
  def isolated_labels(self) -> list[str]:
    """Labels of nodes no edge touches, in node order."""
    return [node.label for node in self.nodes if node.isolated]

  @property
  def is_sparse(self) -> bool:
    """Whether the graph has fewer edges than nodes (a thin structure)."""
    return bool(self.nodes) and self.edge_count < len(self.nodes)

  @property
  def node_ids(self) -> set[str]:
    """The set of node ids this graph holds, for validating a click selection."""
    return {node.node_id for node in self.nodes}

  def neighbors(self, node_id: str) -> set[str]:
    """Return the ids of nodes an edge joins to ``node_id`` (either direction)."""
    found: set[str] = set()
    for edge in self.edges:
      if edge.source == node_id:
        found.add(edge.target)
      elif edge.target == node_id:
        found.add(edge.source)
    return found


class SankeyFlow(BaseModel):
  """One flow in the limitation follow-up Sankey: from a source to a target."""

  source: int
  target: int
  value: int
  hover: str = ""


class SankeyGraph(BaseModel):
  """
  The limitation follow-up flow, as a Sankey: limitations → addressed / open.

  Node-link diagrams bury a two-column addressed/unaddressed split; a Sankey
  keeps the split legible and still honours the honesty controls the other
  graphs carry.
  """

  title: str
  question: str
  caveat: str
  gap_lens: str
  node_labels: list[str] = []
  node_hovers: list[str] = []
  flows: list[SankeyFlow] = []
  unanswered_count: int = 0
  addressed_count: int = 0
  no_later_count: int = 0  # groups whose source Paper had no later Paper to check

  @property
  def is_empty(self) -> bool:
    """Whether there is any limitation group to flow."""
    return not self.flows

  @property
  def all_open(self) -> bool:
    """Whether every group is still open (the common degenerate case)."""
    return bool(self.flows) and self.addressed_count == 0


def _corpus_papers(manifest: ManifestArtifact) -> list[PaperRecord]:
  """Return the Corpus's Papers (the manifest's whole Paper list)."""
  return list(manifest.papers)


def build_citation_network(manifest: ManifestArtifact) -> NetworkGraph:
  """
  Build the in-Corpus citation network from each Paper's ``referenced_works``.

  A directed edge A→B means Paper A (resolved on OpenAlex) cites Paper B, and B
  is itself in the Corpus. Only in-Corpus edges are drawn, so the graph shows how
  tightly the Corpus cites itself. A node no edge touches is isolated: it is a
  Retrieval-Gap signal (the Corpus may be missing the work that connects it).
  Papers with no OpenAlex id could not be placed and are reported, never assumed
  to be uncited (they need an ingest ``--resolve`` run).
  """
  papers = _corpus_papers(manifest)
  by_openalex = {p.openalex_id: p for p in papers if p.openalex_id}
  unresolved = [p for p in papers if not p.openalex_id]

  nodes: list[GraphNode] = []
  edges: list[GraphEdge] = []
  touched: set[str] = set()
  for paper in papers:
    if not paper.openalex_id:
      continue
    for referenced in set(paper.referenced_works):
      cited = by_openalex.get(referenced)
      if cited is None or cited.citation_key == paper.citation_key:
        continue
      edges.append(
        GraphEdge(
          source=paper.citation_key,
          target=cited.citation_key,
          hover=f"{paper.citation_key} \u2192 {cited.citation_key}",
        )
      )
      touched.add(paper.citation_key)
      touched.add(cited.citation_key)

  for paper in papers:
    if not paper.openalex_id:
      continue
    isolated = paper.citation_key not in touched
    nodes.append(
      GraphNode(
        node_id=paper.citation_key,
        label=short_paper_label(paper),
        weight=1,
        isolated=isolated,
        hover=_citation_hover(paper, isolated),
      )
    )

  notes: list[str] = []
  notes.append(
    text.META_CITATION_DENOMINATOR.format(resolved=len(by_openalex), total=len(papers))
  )
  if unresolved:
    notes.append(
      text.META_CITATION_UNRESOLVED.format(
        count=len(unresolved),
        keys=", ".join(p.citation_key for p in unresolved),
      )
    )
  if nodes and len(edges) < len(nodes):
    notes.append(text.META_CITATION_SPARSE)

  return NetworkGraph(
    kind="citation",
    slug="citation",
    title=text.META_CITATION_TITLE,
    question=text.META_CITATION_QUESTION,
    caveat=text.META_CAVEAT,
    gap_lens=text.META_CITATION_GAP_LENS,
    directed=True,
    nodes=nodes,
    edges=edges,
    notes=notes,
  )


def short_paper_label(paper: PaperRecord) -> str:
  """
  Build a compact, human-readable node label for a Paper.

  A citation key like ``templin2015`` is an opaque id on a graph; a reader wants
  "Templin 2015". The first author's surname and the year read best; the title
  is the fallback (truncated), and the citation key only the last resort. The
  full title and key always stay in the hover card, so nothing is hidden.
  """
  year = f" {paper.year}" if paper.year is not None else ""
  if paper.authors:
    surname = (
      paper.authors[0].split()[-1] if paper.authors[0].split() else paper.authors[0]
    )
    return f"{surname}{year}".strip()
  if paper.title:
    trimmed = paper.title if len(paper.title) <= 30 else paper.title[:29] + "\u2026"
    return f"{trimmed}{year}".strip()
  return paper.citation_key


def _citation_hover(paper: PaperRecord, isolated: bool) -> str:
  """Build the hover card for one citation-network node."""
  lines = [f"<b>{paper.citation_key}</b>"]
  if paper.title:
    lines.append(paper.title)
  if paper.year is not None:
    lines.append(str(paper.year))
  if isolated:
    lines.append(text.META_CITATION_ISOLATED_HOVER)
  return "<br>".join(lines)


def build_cooccurrence(  # pylint: disable=too-many-locals
  normalized: NormalizedFactsArtifact,
  manifest: ManifestArtifact,
  *,
  axis: Literal["topic", "method"],
  max_nodes: int = _MAX_COOCCURRENCE_NODES,
  min_edge_weight: int = 1,
) -> NetworkGraph:
  """
  Build the co-occurrence graph for one axis: categories that share a Paper.

  A node is a category on the axis, sized by how many Corpus Papers it covers; an
  edge joins two categories that at least one Paper covers together, weighted by
  how many Papers do. A pair of categories no Paper combines has no edge: that
  absent edge is the gap (a Knowledge Gap on the Topic axis, a Coverage Gap on
  the Method axis).

  Two controls keep the flagship from becoming a hairball, both honest because
  what they hide is reported: ``max_nodes`` keeps the most-covered categories
  (the rest are noted, not dropped silently), and ``min_edge_weight`` hides links
  backed by fewer Papers than the threshold (a single-Paper co-occurrence is a
  weak signal), counting how many were hidden. Missing pairs are ranked by the
  weaker endpoint's coverage, so a pair of two well-studied categories that no
  Paper combines \u2014 the strongest gap signal \u2014 rises to the top.
  """
  papers_by_category, labels, per_paper = _axis_placements(normalized, manifest, axis)
  axis_label = _AXIS_LABELS.get(axis, axis.capitalize())
  ranked = sorted(
    papers_by_category, key=lambda cid: (-len(papers_by_category[cid]), labels[cid])
  )
  kept = ranked[: max(max_nodes, 1)]
  all_pairs = _cooccurrence_pairs(per_paper, set(kept))
  pair_papers = {p: k for p, k in all_pairs.items() if len(k) >= min_edge_weight}
  hidden_weak = len(all_pairs) - len(pair_papers)

  nodes = [
    GraphNode(
      node_id=cid,
      label=_trim_label(labels[cid]),
      weight=len(papers_by_category[cid]),
      isolated=not any(cid in pair for pair in pair_papers),
      hover=text.META_COOCCURRENCE_NODE_HOVER.format(
        label=labels[cid], count=len(papers_by_category[cid])
      ),
    )
    for cid in kept
  ]
  edges = [
    GraphEdge(
      source=next(iter(pair)),
      target=list(pair)[-1],
      weight=len(keys),
      hover=_pair_hover(pair, labels, keys),
    )
    for pair, keys in pair_papers.items()
  ]
  missing = _missing_pairs(kept, all_pairs, papers_by_category, labels)
  notes = _cooccurrence_notes(
    axis, axis_label, len(nodes), len(ranked), len(kept), hidden_weak, min_edge_weight
  )
  return NetworkGraph(
    kind="cooccurrence",
    slug=f"{axis}-cooccurrence",
    title=_axis_field(axis, text.META_TOPIC_TITLE, text.META_METHOD_TITLE),
    question=_axis_field(axis, text.META_TOPIC_QUESTION, text.META_METHOD_QUESTION),
    caveat=text.META_CAVEAT,
    gap_lens=_axis_field(axis, text.META_TOPIC_GAP_LENS, text.META_METHOD_GAP_LENS),
    directed=False,
    nodes=nodes,
    edges=edges,
    missing_pairs=missing,
    notes=notes,
  )


def _missing_pairs(
  kept: list[str],
  all_pairs: dict[frozenset[str], set[str]],
  papers_by_category: dict[str, set[str]],
  labels: dict[str, str],
) -> list[MissingPair]:
  """Build the ranked list of kept-category pairs no Paper combines."""
  return sorted(
    (
      MissingPair(
        source_id=left,
        target_id=right,
        source_label=labels[left],
        target_label=labels[right],
        strength=min(len(papers_by_category[left]), len(papers_by_category[right])),
      )
      for left, right in combinations(kept, 2)
      if frozenset((left, right)) not in all_pairs
    ),
    key=lambda pair: (-pair.strength, pair.source_label, pair.target_label),
  )


def _cooccurrence_notes(  # pylint: disable=too-many-arguments,too-many-positional-arguments
  axis: str,
  axis_label: str,
  shown_nodes: int,
  total_categories: int,
  kept_categories: int,
  hidden_weak: int,
  min_edge_weight: int,
) -> list[str]:
  """Assemble the denominator, capping, weak-link, and thin-axis notes."""
  notes = [
    text.META_COOCCURRENCE_DENOMINATOR.format(axis=axis_label, total=shown_nodes)
  ]
  if total_categories > kept_categories:
    notes.append(
      text.META_COOCCURRENCE_CAPPED.format(
        shown=kept_categories, total=total_categories, axis=axis_label.lower()
      )
    )
  if hidden_weak:
    notes.append(
      text.META_COOCCURRENCE_WEAK_HIDDEN.format(
        count=hidden_weak, threshold=min_edge_weight
      )
    )
  if axis == "method" and shown_nodes < _THIN_AXIS_NODES:
    notes.append(text.META_METHOD_THIN_NOTE)
  return notes


def _cooccurrence_pairs(
  per_paper: dict[str, set[str]], kept: set[str]
) -> dict[frozenset[str], set[str]]:
  """Count, per kept-category pair, the Papers that cover both categories."""
  pair_papers: dict[frozenset[str], set[str]] = {}
  for key, categories in per_paper.items():
    for left, right in combinations(sorted(categories & kept), 2):
      pair_papers.setdefault(frozenset((left, right)), set()).add(key)
  return pair_papers


def _axis_placements(
  normalized: NormalizedFactsArtifact,
  manifest: ManifestArtifact,
  axis: str,
) -> tuple[dict[str, set[str]], dict[str, str], dict[str, set[str]]]:
  """Gather, for one axis, Papers-per-category, category labels, and per-Paper sets."""
  corpus_keys = {paper.citation_key for paper in manifest.papers}
  papers_by_category: dict[str, set[str]] = {}
  labels: dict[str, str] = {}
  per_paper: dict[str, set[str]] = {}
  for record in normalized.normalized:
    if record.citation_key not in corpus_keys:
      continue
    for assignment in record.assignments:
      if assignment.axis != axis:
        continue
      cid = assignment.category_id
      labels.setdefault(cid, assignment.category_label or cid)
      papers_by_category.setdefault(cid, set()).add(record.citation_key)
      per_paper.setdefault(record.citation_key, set()).add(cid)
  return papers_by_category, labels, per_paper


def _trim_label(label: str) -> str:
  """Trim a long category label for on-graph display; hover keeps the full text."""
  if len(label) <= _MAX_LABEL_CHARS:
    return label
  return label[: _MAX_LABEL_CHARS - 1] + "\u2026"


def _axis_field(axis: str, topic_value: str, method_value: str) -> str:
  """Pick the topic- or method-axis copy for a co-occurrence graph field."""
  return topic_value if axis == "topic" else method_value


def _pair_hover(pair: frozenset[str], labels: dict[str, str], keys: set[str]) -> str:
  """Build the hover card for one co-occurrence edge."""
  left, right = sorted(pair)
  return text.META_COOCCURRENCE_EDGE_HOVER.format(
    left=labels[left], right=labels[right], count=len(keys)
  )


class AuthorRow(BaseModel):
  """One author's collaboration record: Papers written and distinct co-authors."""

  author: str
  paper_count: int
  collaborator_count: int


class AuthorCollaborationTable(BaseModel):
  """
  The author collaboration view as a ranked table, not a node-link graph.

  A co-authorship network over a 10-75 Paper Corpus is a dense hairball (a single
  prolific author can touch hundreds of others), and it carries no gap signal to
  justify the clutter \u2014 collaboration is field-meta. A table answers the same
  question legibly: who publishes the most here, and how broadly do they
  collaborate. Rows are ranked by Paper count; the view keeps its denominators
  and says, in ``gap_lens``, that it is explicitly not a gap.
  """

  title: str
  question: str
  caveat: str
  gap_lens: str
  rows: list[AuthorRow] = []
  total_authors: int = 0
  papers_with_authors: int = 0
  papers_without_authors: int = 0
  notes: list[str] = []

  @property
  def is_empty(self) -> bool:
    """Whether any author could be listed."""
    return not self.rows


def build_author_table(
  manifest: ManifestArtifact, *, top_n: int = AUTHOR_TABLE_ROWS
) -> AuthorCollaborationTable:
  """
  Rank the Corpus's authors by Papers written, with their distinct co-authors.

  Replaces the co-authorship node-link graph (an unreadable hairball at Corpus
  scale) with the same information as a table. Every count is over the Papers
  that list authors; Papers with none are reported, never assumed solo.
  """
  papers = [p for p in manifest.papers if p.authors]
  papers_by_author: dict[str, int] = {}
  collaborators: dict[str, set[str]] = {}
  for paper in papers:
    authors = sorted(set(paper.authors))
    for author in authors:
      papers_by_author[author] = papers_by_author.get(author, 0) + 1
      collaborators.setdefault(author, set()).update(a for a in authors if a != author)

  ranked = sorted(papers_by_author.items(), key=lambda kv: (-kv[1], kv[0]))
  rows = [
    AuthorRow(
      author=author,
      paper_count=count,
      collaborator_count=len(collaborators.get(author, set())),
    )
    for author, count in ranked[: max(top_n, 1)]
  ]

  without_authors = len(manifest.papers) - len(papers)
  notes = [
    text.META_COLLABORATION_DENOMINATOR.format(
      authors=len(papers_by_author), papers=len(papers)
    )
  ]
  if len(ranked) > len(rows):
    notes.append(
      text.META_COLLABORATION_TABLE_CAPPED.format(shown=len(rows), total=len(ranked))
    )
  if without_authors:
    notes.append(text.META_COLLABORATION_MISSING.format(count=without_authors))

  return AuthorCollaborationTable(
    title=text.META_COLLABORATION_TITLE,
    question=text.META_COLLABORATION_QUESTION,
    caveat=text.META_CAVEAT,
    gap_lens=text.META_COLLABORATION_GAP_LENS,
    rows=rows,
    total_authors=len(papers_by_author),
    papers_with_authors=len(papers),
    papers_without_authors=without_authors,
    notes=notes,
  )


def build_limitation_flow(limitations: LimitationsData) -> SankeyGraph:
  """
  Build the limitation follow-up Sankey from the Unanswered Limitations data.

  Each limitation group flows to one of two sinks: "addressed by a later Paper"
  or "still open". The open sink is the Unanswered-Limitation gap signal, kept
  visually distinct from the addressed flow. A Sankey beats the node-link idiom
  here because the addressed/open split is the whole point, and a two-column flow
  reads it at a glance where a force-directed layout would scatter it.
  """
  node_labels = [text.META_LIMITATION_ADDRESSED_NODE, text.META_LIMITATION_OPEN_NODE]
  node_hovers = ["", ""]
  flows: list[SankeyFlow] = []
  for group in limitations.groups:
    node_hovers.append(
      text.META_LIMITATION_GROUP_HOVER.format(
        label=group.label or group.group_id,
        sources=group.source_paper_count,
        follow_ups=group.follow_up_count,
      )
    )
    flows.append(_limitation_flow(group, len(node_labels)))
    node_labels.append(group.label or group.group_id)

  addressed = sum(1 for group in limitations.groups if group.addressed)
  no_later = sum(1 for group in limitations.groups if group.later_paper_count == 0)
  return SankeyGraph(
    title=text.META_LIMITATION_TITLE,
    question=text.META_LIMITATION_QUESTION,
    caveat=text.META_CAVEAT,
    gap_lens=text.META_LIMITATION_GAP_LENS,
    node_labels=node_labels,
    node_hovers=node_hovers,
    flows=flows,
    unanswered_count=len(limitations.groups) - addressed,
    addressed_count=addressed,
    no_later_count=no_later,
  )


# The Sankey's two sinks: flow 0 is "addressed by a later Paper", flow 1 is open.
_ADDRESSED_SINK, _OPEN_SINK = 0, 1


def _limitation_flow(group: LimitationGroupView, group_index: int) -> SankeyFlow:
  """Build the flow from one limitation group to the addressed or open sink."""
  label = group.label or group.group_id
  if group.addressed:
    return SankeyFlow(
      source=group_index,
      target=_ADDRESSED_SINK,
      value=group.follow_up_count or 1,
      hover=text.META_LIMITATION_ADDRESSED_FLOW.format(
        label=label, count=group.follow_up_count
      ),
    )
  return SankeyFlow(
    source=group_index,
    target=_OPEN_SINK,
    value=1,
    hover=text.META_LIMITATION_OPEN_FLOW.format(label=label),
  )


def _layout(graph: NetworkGraph) -> dict[str, tuple[float, float]]:
  """Compute reproducible 2-D node positions with networkx's spring layout."""
  builder = nx.DiGraph if graph.directed else nx.Graph
  nx_graph = builder()
  nx_graph.add_nodes_from(node.node_id for node in graph.nodes)
  nx_graph.add_edges_from((edge.source, edge.target) for edge in graph.edges)
  if nx_graph.number_of_nodes() == 0:
    return {}
  return {
    node_id: (float(pos[0]), float(pos[1]))
    for node_id, pos in nx.spring_layout(nx_graph, seed=_LAYOUT_SEED).items()
  }


def node_role(graph: NetworkGraph, node: GraphNode, selected_id: str | None) -> str:
  """
  Classify a node for rendering, given the clicked selection (if any).

  With no selection a node reads as ``isolated`` or ``connected`` (its gap
  signal). With a selection the clicked node is ``selected``, its edge-neighbours
  are ``neighbor``, and everything else is ``faded`` so the click-to-highlight
  interaction makes the selected node's neighbourhood legible.
  """
  if selected_id is None or selected_id not in graph.node_ids:
    return "isolated" if node.isolated else "connected"
  if node.node_id == selected_id:
    return "selected"
  if node.node_id in graph.neighbors(selected_id):
    return "neighbor"
  return "faded"


# Per-role marker styling: fill colour, legend name, symbol, and opacity.
_ROLE_STYLE: dict[str, tuple[str, str, str, float]] = {
  "connected": (text.META_CONNECTED_COLOR, text.META_CONNECTED_LEGEND, "circle", 1.0),
  "isolated": (text.META_ISOLATED_COLOR, text.META_ISOLATED_LEGEND, "diamond", 1.0),
  "selected": (text.META_SELECTED_COLOR, text.META_SELECTED_LEGEND, "star", 1.0),
  "neighbor": (text.META_CONNECTED_COLOR, text.META_NEIGHBOR_LEGEND, "circle", 1.0),
  "faded": ("rgba(150,150,150,0.35)", text.META_FADED_LEGEND, "circle", 0.35),
}


def build_network_figure(
  graph: NetworkGraph, *, selected_id: str | None = None, thumbnail: bool = False
) -> go.Figure:
  """
  Render a network graph as a Plotly figure: edges behind, nodes in front.

  Isolated nodes (and, for the citation network, nodes with no in-Corpus edge)
  are drawn in a distinct colour so the eye catches the gap signal. Edge width
  scales with weight so a co-occurrence backed by many Papers reads as heavier
  than a one-Paper link. When ``selected_id`` names a clicked node, that node and
  its neighbours stay vivid while the rest fade, and the edges touching it are
  drawn last, thicker and in the selected colour, so click-to-highlight makes the
  connections \u2014 not just the nodes \u2014 stand out. Hover carries each node's
  and edge's metadata; the accessible data table the layout shows alongside is the
  non-interactive fallback (the figure is never the only way to read the numbers).

  ``thumbnail`` renders a clean miniature for a gallery tile: no node labels, no
  legend, smaller markers and thinner edges, so a dense graph reads as a shape at
  a glance instead of a tangle of overlapping text.
  """
  positions = _layout(graph)
  figure = go.Figure()
  active = selected_id if selected_id in graph.node_ids else None

  # Draw the background edges first and the selected node's edges last, so the
  # highlighted arcs sit on top of everything rather than behind faded links.
  background = [
    e for e in graph.edges if active is None or active not in (e.source, e.target)
  ]
  incident = [
    e for e in graph.edges if active is not None and active in (e.source, e.target)
  ]
  for edge in background:
    faded = active is not None
    colour = "rgba(120,120,120,0.08)" if faded else "rgba(120,120,120,0.45)"
    _add_edge_trace(figure, edge, positions, colour=colour, thumbnail=thumbnail)
  for edge in incident:
    _add_edge_trace(
      figure,
      edge,
      positions,
      colour=text.META_SELECTED_COLOR,
      thumbnail=thumbnail,
      emphasis=3,
    )

  roles: dict[str, list[GraphNode]] = {}
  for node in graph.nodes:
    roles.setdefault(node_role(graph, node, active), []).append(node)
  for role in ("faded", "connected", "isolated", "neighbor", "selected"):
    _add_node_trace(
      figure, roles.get(role, []), positions, role=role, thumbnail=thumbnail
    )

  figure.update_layout(
    showlegend=not thumbnail,
    legend_title=text.META_LEGEND_TITLE,
    xaxis={"visible": False},
    yaxis={"visible": False},
    margin={"l": 10, "r": 10, "t": 10, "b": 10},
  )
  return figure


def _add_edge_trace(
  figure: go.Figure,
  edge: GraphEdge,
  positions: dict[str, tuple[float, float]],
  *,
  colour: str,
  thumbnail: bool,
  emphasis: int = 0,
) -> None:
  """Add one edge line, thinner in a thumbnail and thicker when emphasised."""
  x0, y0 = positions[edge.source]
  x1, y1 = positions[edge.target]
  base = 0.6 if thumbnail else min(1 + edge.weight, 8)
  figure.add_trace(
    go.Scatter(
      x=[x0, x1],
      y=[y0, y1],
      mode="lines",
      line={"width": base + emphasis, "color": colour},
      hoverinfo="skip" if thumbnail else "text",
      text=None if thumbnail else edge.hover,
      showlegend=False,
    )
  )


def _add_node_trace(
  figure: go.Figure,
  nodes: list[GraphNode],
  positions: dict[str, tuple[float, float]],
  *,
  role: str,
  thumbnail: bool = False,
) -> None:
  """Add one node trace for a rendering role, carrying node ids as customdata."""
  if not nodes:
    return
  colour, name, symbol, opacity = _ROLE_STYLE[role]
  if thumbnail:
    sizes = [min(5 + 1.5 * node.weight, 14) for node in nodes]
    figure.add_trace(
      go.Scatter(
        x=[positions[node.node_id][0] for node in nodes],
        y=[positions[node.node_id][1] for node in nodes],
        mode="markers",
        marker={
          "size": sizes,
          "color": colour,
          "opacity": opacity,
          "line": {"width": 0.5, "color": "white"},
          "symbol": symbol,
        },
        hoverinfo="skip",
        showlegend=False,
      )
    )
    return
  figure.add_trace(
    go.Scatter(
      x=[positions[node.node_id][0] for node in nodes],
      y=[positions[node.node_id][1] for node in nodes],
      mode="markers+text",
      marker={
        "size": [min(12 + 4 * node.weight, 40) for node in nodes],
        "color": colour,
        "opacity": opacity,
        "line": {"width": 1, "color": "white"},
        "symbol": symbol,
      },
      text=[node.label for node in nodes],
      textposition="top center",
      hoverinfo="text",
      hovertext=[node.hover for node in nodes],
      customdata=[node.node_id for node in nodes],
      name=name,
    )
  )


def build_sankey_figure(graph: SankeyGraph) -> go.Figure:
  """Render the limitation follow-up flow as a Plotly Sankey diagram."""
  node_colours = [
    text.META_ISOLATED_COLOR
    if index == _OPEN_SINK
    else text.META_CONNECTED_COLOR
    if index == _ADDRESSED_SINK
    else "rgba(140,140,140,0.75)"
    for index in range(len(graph.node_labels))
  ]
  link_colours = [
    "rgba(214,39,40,0.35)" if flow.target == _OPEN_SINK else "rgba(44,160,44,0.35)"
    for flow in graph.flows
  ]
  figure = go.Figure(
    go.Sankey(
      node={
        "label": graph.node_labels,
        "color": node_colours,
        "customdata": graph.node_hovers,
        "hovertemplate": "%{customdata}<extra></extra>",
        "pad": 12,
      },
      link={
        "source": [flow.source for flow in graph.flows],
        "target": [flow.target for flow in graph.flows],
        "value": [flow.value for flow in graph.flows],
        "color": link_colours,
        "customdata": [flow.hover for flow in graph.flows],
        "hovertemplate": "%{customdata}<extra></extra>",
      },
    )
  )
  figure.update_layout(margin={"l": 10, "r": 10, "t": 10, "b": 10})
  return figure
