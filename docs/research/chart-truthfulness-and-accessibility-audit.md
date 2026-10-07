# Chart audit — truthfulness, colour-blind safety, missing data, denominator

Plan step 3. The dashboard's charts audited with the `scientific-visualization`
skill, layered on the integrity rules I2 (denominator) and I3 (traceability)
from `docs/research/ux-heuristics-for-evidence-dashboards.md`. This is a
different lens from `meta-analysis-graph-audit.md` (which asked "is the graph
*useful*"): here the questions are **does the encoding tell the truth**, **can a
colour-blind reader read it**, **is missing data distinguished from zero**, and
**does the denominator travel with the figure**.

- **Audited at:** commit `989d73a`, branch `feat/49-meta-analysis`.
- **Method:** read the figure builders in `meta_analysis.py` and `coverage.py`
  and the colour constants in `text.py`; ran the skill's `palette_audit.py`
  (WCAG sRGB contrast + pairwise CIE L\* grayscale screening, network-free) on
  the two real palettes. Numbers below are from that tool.
- **Not done here:** static export / Kaleido rendering (the dashboard is
  interactive-only; Chrome/Kaleido not assumed). Visual confirmation at rendered
  size is a human `just dashboard` pass.

## Charts in the current UI

| Chart | Builder | Encoding |
| --- | --- | --- |
| Trends (publication volume per Topic over time) | `coverage.py build_trends_figure` | multi-line, Plotly default colour cycle; emerging solid, abandoned dashed, steady thin |
| Citation / Topic / Method network | `meta_analysis.py build_network_figure` | node-link; node role by colour **and** symbol |
| Limitation follow-up | `meta_analysis.py build_limitation_bar_figure` | one horizontal stacked bar, three buckets |
| Author collaboration | `build_author_table` | ranked table (not a chart — was a hairball, correctly demoted) |
| Overview mini-bars + panel bars | `app.py _render_mini_bar`, `st.bar_chart` | bar charts |

**Finding C0 (scope correction, severity 2).** The plan, `docs/journeys.md`, and
`docs/FUNCTIONALITY.md` all name a **Coverage Matrix heatmap**. It is **not
rendered anywhere**. It was **removed deliberately** in `d6c4698` ("remove
Coverage & Trends page"): Trends moved into the Meta-Analysis gallery and the
heatmap builders were dropped as dead code. The Coverage Matrix now exists only
as a data model (`artifacts.py CoverageMatrixRecord`), surfaced as co-occurrence
*networks* and gap cards. The removal is worth revisiting for accessibility: a
Topic×Method heatmap with empty cells marked is *position on a common scale*,
the honest, most readable gap encoding the skill prefers, while a node-link
graph is a weaker substitute (layout-dependent, harder to read, no common
baseline). `docs/FUNCTIONALITY.md` was stale and has been corrected.

## What the charts get right

- **Missing ≠ zero (truthfulness).** The network builders explicitly treat an
  absent edge as "no Paper links these two, a fact about the Corpus, never a
  value of zero", and surface missing edges and isolated nodes rather than
  hiding them. This is exactly the skill's "distinguish missing / zero /
  censored" guardrail, done deliberately.
- **Denominator travels (I2).** Every chart carries its denominator as a caption
  or note: Trends states "{with_year} of {total} Papers … carry a year" and
  reports year-less Papers separately; networks carry node/edge/isolated counts
  and capping notes; the limitation bar carries addressed/open/no-later counts.
- **Reproducible layout.** `_LAYOUT_SEED = 42` fixes the network layout so the
  same Corpus always draws the same figure — reproducibility treated as honesty.
- **Honest baselines.** Bars measure from zero (`rangemode: tozero`; count
  bars); no truncated-axis exaggeration found.
- **Accessible data alternative (partial).** Every network graph ships a data
  table fallback (`_render_graph_data_table`) and a text figure summary — the
  skill's "underlying data for web delivery" for the node-link charts.
- **Redundant encoding on node role.** Node role is colour **and** symbol:
  connected = circle, isolated = diamond, selected = star. Colour is not the only
  channel for the gap signal (isolated nodes), which rescues the weak palette
  below.

## Colour-blind safety — measured

### Network nodes — connected `#2C7FB8` / isolated `#D62728` / selected `#E6A817`

| Pair | WCAG contrast | Grayscale ΔL\* | Screen |
| --- | --- | --- | --- |
| connected (blue) vs isolated (red) | 1.157 | **4.05** | **review** (< 10) |
| connected (blue) vs selected (amber) | 2.064 | 21.9 | pass |
| isolated (red) vs selected (amber) | 2.387 | 26.0 | pass |

| Colour | Contrast vs white | Screen |
| --- | --- | --- |
| connected `#2C7FB8` | 4.34 | pass |
| isolated `#D62728` | 5.02 | pass |
| selected `#E6A817` | 2.11 | **review** (< 3.0 graphical) |

- **C1 (severity 2).** The gap signal is **isolated = red vs connected = blue**,
  and these two are nearly equiluminant (ΔL\* 4.05): under monochrome print or
  some colour-vision conditions they're hard to separate by colour alone.
  **Mitigated** by the diamond-vs-circle symbol, so this is severity 2, not 3 —
  but the symbol is doing all the real work; the colour pairing adds little.
  Consider an Okabe–Ito / Paul Tol pair (e.g. isolated `#D55E00` vs connected
  `#0072B2`) for a larger luminance gap.
- **C2 (severity 1).** Selected amber `#E6A817` is below the 3:1 graphical
  contrast threshold on white (2.11). Mitigated by the star symbol + it being a
  transient click state; darken slightly for a cleaner highlight.

### Limitation follow-up bar — addressed `#2CA02C` / open `#D62728` / no-later `#9AA0A6`

| Pair | Grayscale ΔL\* | Screen |
| --- | --- | --- |
| addressed (green) vs open (red) | 11.06 | pass (barely) |
| addressed (green) vs no-later (grey) | 7.66 | **review** (< 10) |
| open (red) vs no-later (grey) | 18.7 | pass |

- **C3 (severity 3).** The two most meaningful buckets — **addressed (good) =
  green** and **open, the gap signal = red** — are the textbook **red/green**
  pairing, the most common colour-vision confusion (deutan/protan). Their
  luminance gap is small (ΔL\* 11). **Partially mitigated**: each segment carries
  its count as inside text plus a legend and stack position, so the numbers are
  readable without colour — but the *semantic* good/bad split rides on red/green.
  This is the clearest colour finding; a CVD-safe diverging choice (e.g. teal
  `#009E73` addressed / orange `#D55E00` open, Okabe–Ito) removes it. Severity 3
  because this bucket split is the chart's whole message.
- **C4 (severity 1).** No-later grey `#9AA0A6` is low-contrast on white (2.64)
  and close to green in grayscale (ΔL\* 7.66); a hatch/pattern or darker grey
  would separate "not yet checkable" from "addressed" more safely.

### Trends line chart

- **C5 (severity 3).** Topic *status* is encoded redundantly (emerging solid /
  abandoned dashed / steady thin + a `(emerging)`/`(abandoned)` legend suffix) —
  good. But individual **Topic identity** rides on the **Plotly default colour
  cycle**, which is not colour-blind safe, with one marker symbol for all lines
  and no direct labels. With more than ~8–10 Topics the cycle repeats and lines
  become indistinguishable for any reader. Fix: a CVD-safe qualitative palette
  (Okabe–Ito 8 / Paul Tol), per-line marker symbols, or direct end-of-line
  labels; cap the number of lines and move the rest to a table, as the
  co-occurrence graphs already do.

### Overview mini-bars

- **C6 (severity 2).** `_render_mini_bar` hides the x axis entirely and puts
  category names only on hover, with value labels on the bars. Hover is not a
  label substitute (skill) and is not keyboard-accessible; the expanded panel
  (full `st.bar_chart`) is the fallback, so severity 2 — but the thumbnail alone
  misleads about which bar is which until opened.

## Accessibility gaps across all charts

- **C7 (severity 2).** No **alt text / long description** on any figure. Plotly
  charts embedded via `st.plotly_chart` expose none. The network data tables and
  text summaries partially cover the node-link charts; Trends, the limitation
  bar, and the mini-bars have no text alternative. Add a short text summary
  beside each (the networks already model one — extend the pattern).
- **C8 (severity 2).** **Interactive-only, no static export.** The dashboard
  renders only live Plotly; there is no static figure export, no DPI/dimension
  control, no provenance manifest. For a tool whose output a researcher may put
  in a review or grant, there is no citable figure artifact. This ties to the
  "Write / Export" step in `docs/journeys.md` (export Markdown/figures of
  accepted gaps) — a chart export path belongs in that work. The skill treats
  static and interactive as distinct deliverables; today only one exists.

## Severity rollup

**Severity 3:**
- **C3** — limitation bar encodes the good/bad split as red/green (the key
  message rides on the most common CVD confusion). *Cheap: swap to Okabe–Ito.*
- **C5** — Trends topic identity uses the non-CVD-safe Plotly default cycle with
  no redundant channel and no line cap.

**Severity 2:**
- **C0** — the Coverage Matrix heatmap named in the docs is not rendered; the
  strongest accessible gap encoding is absent.
- **C1** — network isolated/connected colours are near-equiluminant (symbol
  saves it).
- **C6** — overview mini-bars rely on hover for category identity.
- **C7** — no alt text / text alternative on Trends, limitation bar, mini-bars.
- **C8** — no static, citable figure export path.

**Severity 1:**
- **C2** — selected amber below graphical contrast on white (symbol saves it).
- **C4** — no-later grey low-contrast and close to green in grayscale.

**Not a defect — recorded strengths:** missing ≠ zero; denominator on every
chart; reproducible seed; honest zero baselines; node data-table fallbacks;
colour+symbol redundancy on node role.

## What feeds the next steps

- **Prototype (step 4) / wayfinder (step 6):** C0 (bring back a Coverage Matrix
  heatmap?) and C8 (figure export in the "Write" step) are design decisions, not
  just fixes.
- **Cheap fixes, no ADR:** C3 and C5 (swap to a CVD-safe palette + add a
  redundant channel / line cap), C2/C4 (darken amber/grey), C7 (text summaries).
  These become tickets off wayfinder.
- Palette source for the fixes: the skill's `references/color_palettes.md` and
  `assets/color_palettes.py` (Okabe–Ito, Paul Tol).

> Note: the `scientific-visualization` skill asks contributors to cite its own
> paper. Per `AGENTS.md` that citation must not enter product output or docs; it
> is deliberately omitted here.
