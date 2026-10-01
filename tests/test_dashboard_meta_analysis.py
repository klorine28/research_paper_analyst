"""
Behavior of the Field Meta-Analysis layer: artifacts in, figures out (#49).

The six graphs are built with networkx and rendered with Plotly. These tests
drive the pure builders through the dashboard's own read models (the same shapes
the layout loads from disk), so a schema drift between an artifact writer and
these readers would surface here. The through-line every test guards is the
honesty control the issue mandates: an absent edge is a fact about the Corpus,
never a measured zero, and every graph names the question it answers and carries
the incomplete-Corpus caveat.
"""

import plotly.graph_objects as go

from research_gap_dashboard.dashboard.artifacts import (
  AssignmentRecord,
  ManifestArtifact,
  NormalizedFactsArtifact,
  NormalizedFactsRecord,
  PaperRecord,
)
from research_gap_dashboard.dashboard.limitations import (
  FollowUpPassage,
  LimitationGroupView,
  LimitationsData,
  SourceStatement,
)
from research_gap_dashboard.dashboard.meta_analysis import (
  build_author_table,
  build_citation_network,
  build_cooccurrence,
  build_limitation_flow,
  build_network_figure,
  build_sankey_figure,
  node_role,
  short_paper_label,
)


def _manifest(papers: list[PaperRecord]) -> ManifestArtifact:
  """Wrap a list of Paper read models as a CorpusManifest read model."""
  return ManifestArtifact(corpus_root="/tmp/corpus", papers=papers)


def _normalized(
  placements: dict[str, list[tuple[str, str, str]]],
) -> NormalizedFactsArtifact:
  """Build NormalizedFacts from {key: [(axis, category_id, label)]} placements."""
  return NormalizedFactsArtifact(
    corpus_root="/tmp/corpus",
    normalized=[
      NormalizedFactsRecord(
        citation_key=key,
        assignments=[
          AssignmentRecord(axis=axis, category_id=cid, category_label=label)
          for axis, cid, label in rows
        ],
      )
      for key, rows in placements.items()
    ],
  )


# --- Graph 1: Citation network -------------------------------------------------


def test_citation_network_draws_only_in_corpus_edges():
  """An edge is drawn only when both citer and cited are Papers in the Corpus."""
  papers = [
    PaperRecord(
      citation_key="p01",
      doi="10.1/p01",
      openalex_id="W1",
      referenced_works=["W2", "Wx"],
    ),
    PaperRecord(citation_key="p02", doi="10.1/p02", openalex_id="W2"),
  ]
  graph = build_citation_network(_manifest(papers))

  # p01 -> p02 is in-Corpus; p01 -> Wx points outside and is not drawn.
  assert [(e.source, e.target) for e in graph.edges] == [("p01", "p02")]
  assert graph.directed is True


def test_citation_network_flags_isolated_nodes_as_retrieval_gap():
  """A resolved Paper no in-Corpus citation touches is marked isolated."""
  papers = [
    PaperRecord(
      citation_key="p01", doi="10.1/p01", openalex_id="W1", referenced_works=["W2"]
    ),
    PaperRecord(citation_key="p02", doi="10.1/p02", openalex_id="W2"),
    PaperRecord(citation_key="p03", doi="10.1/p03", openalex_id="W3"),
  ]
  graph = build_citation_network(_manifest(papers))

  assert graph.isolated_labels == ["p03"]
  assert "Retrieval Gap" in graph.gap_lens


def test_citation_network_reports_unresolved_papers_rather_than_dropping_them():
  """A Paper with no OpenAlex id is named in a note, not silently omitted."""
  papers = [
    PaperRecord(citation_key="p01", doi="10.1/p01", openalex_id="W1"),
    PaperRecord(citation_key="p02", doi="10.1/p02"),  # no resolve
  ]
  graph = build_citation_network(_manifest(papers))

  assert [n.node_id for n in graph.nodes] == ["p01"]
  assert any("p02" in note for note in graph.notes)


def test_citation_network_notes_sparse_structure():
  """Fewer in-Corpus citations than Papers earns the sparse-structure note."""
  papers = [
    PaperRecord(citation_key=f"p0{i}", doi=f"10.1/p0{i}", openalex_id=f"W{i}")
    for i in range(1, 4)
  ]
  graph = build_citation_network(_manifest(papers))

  assert graph.is_sparse
  assert any("Sparse citation structure" in note for note in graph.notes)


# --- Graph 1: readable node labels --------------------------------------------


def test_short_paper_label_prefers_first_author_surname_and_year():
  """A node reads 'Surname Year', not the opaque citation key."""
  paper = PaperRecord(
    citation_key="templin2015", doi="d", authors=["Christian Templin"], year=2015
  )
  assert short_paper_label(paper) == "Templin 2015"


def test_short_paper_label_falls_back_to_title_then_key():
  """With no authors the label uses a trimmed title, then the key as last resort."""
  titled = PaperRecord(citation_key="k", doi="d", title="A Study", year=2020)
  bare = PaperRecord(citation_key="k2", doi="d")
  assert short_paper_label(titled) == "A Study 2020"
  assert short_paper_label(bare) == "k2"


def test_citation_nodes_use_readable_labels_keeping_the_key_in_hover():
  """The citation graph labels nodes readably but keeps the key in the hover."""
  papers = [
    PaperRecord(
      citation_key="templin2015",
      doi="d",
      openalex_id="W1",
      authors=["Christian Templin"],
      year=2015,
    )
  ]
  graph = build_citation_network(_manifest(papers))
  node = graph.nodes[0]
  assert node.label == "Templin 2015"
  assert "templin2015" in node.hover


# --- Graphs 2 & 6: Topic and Method co-occurrence ------------------------------


def test_topic_cooccurrence_joins_topics_that_share_a_paper():
  """Two Topics one Paper covers together get a weighted edge between them."""
  normalized = _normalized(
    {
      "p01": [("topic", "a", "Alpha"), ("topic", "b", "Beta")],
      "p02": [("topic", "a", "Alpha"), ("topic", "b", "Beta")],
      "p03": [("topic", "a", "Alpha"), ("topic", "c", "Gamma")],
    }
  )
  manifest = _manifest(
    [PaperRecord(citation_key=k, doi=k) for k in ("p01", "p02", "p03")]
  )
  graph = build_cooccurrence(normalized, manifest, axis="topic")

  weights = {frozenset((e.source, e.target)): e.weight for e in graph.edges}
  assert weights[frozenset(("a", "b"))] == 2  # p01 and p02
  assert weights[frozenset(("a", "c"))] == 1  # p03


def test_topic_cooccurrence_surfaces_missing_pairs_as_knowledge_gaps():
  """A Topic pair no Paper combines is reported as a missing pair, not an edge."""
  normalized = _normalized(
    {
      "p01": [("topic", "a", "Alpha"), ("topic", "b", "Beta")],
      "p02": [("topic", "c", "Gamma")],
    }
  )
  manifest = _manifest([PaperRecord(citation_key=k, doi=k) for k in ("p01", "p02")])
  graph = build_cooccurrence(normalized, manifest, axis="topic")

  missing = {frozenset((m.source_label, m.target_label)) for m in graph.missing_pairs}
  assert frozenset(("Alpha", "Gamma")) in missing
  assert frozenset(("Beta", "Gamma")) in missing
  assert frozenset(("Alpha", "Beta")) not in missing  # they co-occur
  assert "Knowledge Gap" in graph.gap_lens


def test_cooccurrence_min_edge_weight_hides_weak_links_and_reports_them():
  """Raising the threshold hides single-Paper links and notes how many."""
  normalized = _normalized(
    {
      "p01": [("topic", "a", "Alpha"), ("topic", "b", "Beta")],
      "p02": [("topic", "a", "Alpha"), ("topic", "b", "Beta")],
      "p03": [("topic", "a", "Alpha"), ("topic", "c", "Gamma")],  # weight-1 a-c
    }
  )
  manifest = _manifest(
    [PaperRecord(citation_key=k, doi=k) for k in ("p01", "p02", "p03")]
  )
  graph = build_cooccurrence(normalized, manifest, axis="topic", min_edge_weight=2)

  pairs = {frozenset((e.source, e.target)) for e in graph.edges}
  assert frozenset(("a", "b")) in pairs  # weight 2, kept
  assert frozenset(("a", "c")) not in pairs  # weight 1, hidden
  assert any("hidden" in note for note in graph.notes)


def test_cooccurrence_max_nodes_keeps_the_most_covered_categories():
  """The node cap keeps the best-covered categories and reports the trim."""
  normalized = _normalized(
    {
      "p01": [("topic", "a", "Alpha")],
      "p02": [("topic", "a", "Alpha")],
      "p03": [("topic", "b", "Beta")],
      "p04": [("topic", "c", "Gamma")],
    }
  )
  manifest = _manifest(
    [PaperRecord(citation_key=k, doi=k) for k in ("p01", "p02", "p03", "p04")]
  )
  graph = build_cooccurrence(normalized, manifest, axis="topic", max_nodes=1)

  assert {n.node_id for n in graph.nodes} == {"a"}  # most-covered kept
  assert any("most-covered" in note for note in graph.notes)


def test_missing_pairs_rank_by_strength_of_both_endpoints():
  """A pair of two well-studied topics ranks above one touching a rare topic."""
  normalized = _normalized(
    {
      "p01": [("topic", "a", "Alpha")],
      "p02": [("topic", "a", "Alpha")],
      "p03": [("topic", "b", "Beta")],
      "p04": [("topic", "b", "Beta")],
      "p05": [("topic", "c", "Gamma")],  # rare
    }
  )
  manifest = _manifest(
    [PaperRecord(citation_key=f"p{i:02d}", doi=f"p{i}") for i in range(1, 6)]
  )
  graph = build_cooccurrence(normalized, manifest, axis="topic")

  first = graph.missing_pairs[0]
  assert {first.source_id, first.target_id} == {"a", "b"}  # strongest gap first
  assert first.strength == 2


def test_method_axis_reads_the_method_placements_and_its_own_gap_lens():
  """The method axis builds from method placements and names Coverage Gaps."""
  normalized = _normalized(
    {
      "p01": [("topic", "a", "Alpha"), ("method", "rct", "RCT")],
      "p02": [("method", "rct", "RCT"), ("method", "cohort", "Cohort")],
    }
  )
  manifest = _manifest([PaperRecord(citation_key=k, doi=k) for k in ("p01", "p02")])
  graph = build_cooccurrence(normalized, manifest, axis="method")

  assert {n.node_id for n in graph.nodes} == {"rct", "cohort"}
  assert "Coverage Gap" in graph.gap_lens


def test_cooccurrence_ignores_papers_outside_the_corpus():
  """A NormalizedFacts record for a non-Corpus key contributes nothing."""
  normalized = _normalized(
    {
      "p01": [("topic", "a", "Alpha"), ("topic", "b", "Beta")],
      "ghost": [("topic", "a", "Alpha"), ("topic", "z", "Zeta")],
    }
  )
  manifest = _manifest([PaperRecord(citation_key="p01", doi="p01")])
  graph = build_cooccurrence(normalized, manifest, axis="topic")

  assert {n.node_id for n in graph.nodes} == {"a", "b"}


# --- Graph 4: Author collaboration (now a ranked table) -----------------------


def test_author_table_ranks_by_papers_and_counts_distinct_collaborators():
  """The table ranks authors by Papers written and lists their distinct co-authors."""
  papers = [
    PaperRecord(citation_key="p01", doi="p01", authors=["Ng", "Lee"]),
    PaperRecord(citation_key="p02", doi="p02", authors=["Ng", "Park"]),
  ]
  table = build_author_table(_manifest(papers))

  top = table.rows[0]
  assert top.author == "Ng" and top.paper_count == 2
  assert top.collaborator_count == 2  # Lee and Park
  assert "not a gap" in table.gap_lens.lower()


def test_author_table_caps_rows_and_reports_papers_without_authors():
  """The table caps to top-N (reporting the cap) and counts author-less Papers."""
  papers = [
    PaperRecord(citation_key=f"p{i:02d}", doi=f"p{i}", authors=[f"A{i}"])
    for i in range(30)
  ]
  papers.append(PaperRecord(citation_key="p99", doi="p99"))  # no authors
  table = build_author_table(_manifest(papers), top_n=20)

  assert len(table.rows) == 20
  assert table.papers_without_authors == 1
  assert any("1 Paper" in note for note in table.notes)
  assert any("most-published" in note for note in table.notes)


# --- Graph 5: Limitation follow-up (Sankey) -----------------------------------


def _limitations() -> LimitationsData:
  """Two groups: one addressed by a later Paper, one still open."""
  addressed = LimitationGroupView(
    group_id="g1",
    label="Small samples",
    earliest_year=2018,
    source_citation_keys=["p01"],
    later_paper_count=1,
    follow_up_count=1,
    statements=[
      SourceStatement(citation_key="p01", statement="", passage="", section="")
    ],
    follow_ups=[FollowUpPassage(citation_key="p02", passage="", section="", reason="")],
  )
  still_open = LimitationGroupView(
    group_id="g2",
    label="No long-term data",
    earliest_year=2019,
    source_citation_keys=["p03"],
    later_paper_count=0,
    follow_up_count=0,
    statements=[
      SourceStatement(citation_key="p03", statement="", passage="", section="")
    ],
    follow_ups=[],
  )
  return LimitationsData(groups=[addressed, still_open], extracted_paper_count=3)


def test_limitation_flow_splits_addressed_from_open():
  """Each group flows to the addressed or the open sink, counted honestly."""
  graph = build_limitation_flow(_limitations())

  assert graph.addressed_count == 1
  assert graph.unanswered_count == 1
  # Sink 0 is addressed, sink 1 is open; every group flows to one of them.
  targets = {flow.target for flow in graph.flows}
  assert targets == {0, 1}
  assert "Unanswered Limitation" in graph.gap_lens


def test_limitation_flow_flags_all_open_and_counts_groups_with_no_later_paper():
  """An all-open Corpus is flagged, with how many groups had no later Paper."""
  open_no_later = LimitationGroupView(
    group_id="g1",
    label="Recent-only limitation",
    earliest_year=2024,
    source_citation_keys=["p01"],
    later_paper_count=0,  # nothing published later to check
    follow_up_count=0,
    statements=[
      SourceStatement(citation_key="p01", statement="", passage="", section="")
    ],
    follow_ups=[],
  )
  graph = build_limitation_flow(
    LimitationsData(groups=[open_no_later], extracted_paper_count=1)
  )

  assert graph.all_open
  assert graph.no_later_count == 1


# --- Honesty controls carried by every graph ----------------------------------


def test_every_graph_carries_a_question_and_the_incomplete_corpus_caveat():
  """The governing rule: each chart names its question and inherits the caveat."""
  papers = [
    PaperRecord(citation_key="p01", doi="p01", openalex_id="W1", authors=["Ng"])
  ]
  normalized = _normalized({"p01": [("topic", "a", "Alpha")]})
  manifest = _manifest(papers)
  graphs = [
    build_citation_network(manifest),
    build_cooccurrence(normalized, manifest, axis="topic"),
  ]
  for graph in graphs:
    assert graph.question
    assert "only over the Papers in this Corpus" in graph.caveat

  table = build_author_table(manifest)
  assert table.question
  assert "only over the Papers in this Corpus" in table.caveat

  sankey = build_limitation_flow(_limitations())
  assert sankey.question
  assert "only over the Papers in this Corpus" in sankey.caveat


# --- Click-to-highlight interaction -------------------------------------------


def _topic_graph():
  """Build a small topic co-occurrence graph: a-b co-occur, c stands apart."""
  normalized = _normalized(
    {
      "p01": [("topic", "a", "Alpha"), ("topic", "b", "Beta")],
      "p02": [("topic", "c", "Gamma")],
    }
  )
  manifest = _manifest([PaperRecord(citation_key=k, doi=k) for k in ("p01", "p02")])
  return build_cooccurrence(normalized, manifest, axis="topic")


def test_neighbors_reports_edge_adjacency_in_either_direction():
  """A node's neighbours are every node an edge joins it to."""
  graph = _topic_graph()

  assert graph.neighbors("a") == {"b"}
  assert graph.neighbors("b") == {"a"}
  assert graph.neighbors("c") == set()


def test_node_role_without_selection_reads_connected_or_isolated():
  """With nothing clicked, a node keeps its gap-signal role."""
  graph = _topic_graph()
  by_id = {n.node_id: n for n in graph.nodes}

  assert node_role(graph, by_id["a"], None) == "connected"
  assert node_role(graph, by_id["c"], None) == "isolated"


def test_node_role_with_selection_splits_selected_neighbor_and_faded():
  """Clicking a node makes it selected, its neighbours vivid, the rest faded."""
  graph = _topic_graph()
  by_id = {n.node_id: n for n in graph.nodes}

  assert node_role(graph, by_id["a"], "a") == "selected"
  assert node_role(graph, by_id["b"], "a") == "neighbor"
  assert node_role(graph, by_id["c"], "a") == "faded"


def test_node_role_ignores_a_stale_selection_not_in_the_graph():
  """A selected id no longer in the graph falls back to the no-selection roles."""
  graph = _topic_graph()
  by_id = {n.node_id: n for n in graph.nodes}

  assert node_role(graph, by_id["a"], "ghost") == "connected"


def test_missing_pairs_carry_ids_for_gap_linking():
  """Each missing pair keeps its category ids so the layout can link to a gap."""
  graph = _topic_graph()

  pair = next(
    m for m in graph.missing_pairs if {m.source_id, m.target_id} == {"a", "c"}
  )
  assert pair.source_label in ("Alpha", "Gamma")


def test_selected_figure_still_builds_with_a_highlighted_node():
  """The figure renders when a node is selected (neighbour highlight path)."""
  graph = _topic_graph()
  figure = build_network_figure(graph, selected_id="a")

  assert isinstance(figure, go.Figure)
  assert figure.data


# --- Figures render ------------------------------------------------------------


def test_network_and_sankey_figures_build():
  """The Plotly builders return figures for a non-trivial Corpus."""
  papers = [
    PaperRecord(
      citation_key="p01", doi="p01", openalex_id="W1", referenced_works=["W2"]
    ),
    PaperRecord(citation_key="p02", doi="p02", openalex_id="W2"),
  ]
  network = build_network_figure(build_citation_network(_manifest(papers)))
  sankey = build_sankey_figure(build_limitation_flow(_limitations()))

  assert isinstance(network, go.Figure)
  assert isinstance(sankey, go.Figure)
  assert network.data  # at least one trace (edges and/or nodes)


# --- The meta page renders the six graphs as a collapsible tile gallery --------


def test_meta_page_shows_the_six_graphs_as_gallery_tiles(tmp_path, monkeypatch):
  """The Field Meta-Analysis page presents the six graphs as expandable tiles."""
  from pathlib import Path

  from conftest import StubLlmClient
  from test_detect import _PLACEMENTS, _write_corpus

  import research_gap_dashboard.dashboard as dashboard_pkg
  from research_gap_dashboard.detect import detect_corpus
  from streamlit.testing.v1 import AppTest

  root = _write_corpus(tmp_path / "corpus", _PLACEMENTS)
  detect_corpus(root, StubLlmClient({}))
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(root.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=60).run()
  app.radio[0].set_value("Field Meta-Analysis").run()

  assert not app.exception
  tile_keys = [
    b.key or "" for b in app.button if (b.key or "").startswith("metatile_btn_")
  ]
  tile_ids = {key.removeprefix("metatile_btn_") for key in tile_keys}
  assert tile_ids == {"citation", "topic", "method", "trends", "authors", "limitation"}

  # Opening a tile reveals its full chart below the grid without erroring.
  citation_button = next(
    b for b in app.button if (b.key or "") == "metatile_btn_citation"
  )
  citation_button.click().run()
  assert not app.exception
