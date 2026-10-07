# Journeys: data and user

Three diagrams. The PNGs live in `docs/diagrams/`, rendered from the Mermaid source kept under each image:

1. **Data journey**: how one Paper becomes something the researcher sees.
2. **User experience as it is today**: built from the code, `docs/FUNCTIONALITY.md` and `docs/HANDOFF.md`.
3. **User experience as it should be**: a proposal. Parts marked *(needs ADR)* or
   *(open)* conflict with an accepted ADR or with an open item in `docs/BRIEF.md`.

---

## 1. Data journey: from Paper to user

![Data journey](diagrams/1-data-journey.png)

<details><summary>Mermaid source</summary>

```mermaid
flowchart TD
    subgraph IN["① Researcher's inputs · corpora/&lt;name&gt;/"]
        direction LR
        BIB["corpus.bib / dois.txt"]
        PDF["papers/*.pdf"]
    end

    subgraph PIPE["② Pipeline · CLI stages · each writes JSON to disk"]
        ING["<b>ingest</b><br/>pair records ↔ PDFs"]
        MAN[/"corpus-manifest.json"/]
        RET["<b>retrieve</b> ‹API›<br/>cited-but-missing works"]
        RG[/"retrieval_gaps.json<br/><i>out-of-Corpus</i>"/]
        PAR["<b>parse</b><br/>docling → OCR → pdfplumber"]
        PD[/"paper-data/*.parsed.json<br/>labeled sections"/]
        PR[/"parse-report.json<br/>failures"/]
        EXP["<b>explain</b> ‹LLM›<br/>domain + lay"]
        EXPJ[/"*.explanation.json"/]
        EXR["<b>extract</b> ‹LLM›<br/>7 fields, each with a quote"]
        GATE{"quote found<br/>verbatim?"}
        DROP[/"unverified list<br/><i>dropped, recorded</i>"/]
        EX[/"extractions.json"/]
        AGG["<b>aggregate</b> ‹LLM›<br/>map onto taxonomies"]
        NF[/"normalized_facts.json<br/>+ unmapped terms"/]
        DET["<b>detect</b><br/>sparse Coverage Matrix cells<br/>+ unanswered limitations ‹LLM›"]
        CG[/"candidate_gaps.json"/]

        ING --> MAN
        MAN --> RET --> RG
        MAN --> PAR
        PAR --> PD
        PAR -. "all parsers failed" .-> PR
        PD --> EXP --> EXPJ
        PD --> EXR --> GATE
        GATE -- yes --> EX
        GATE -- no --> DROP
        EX --> AGG --> NF
        NF --> DET
        EX --> DET
        DET --> CG
    end

    subgraph DASH["③ Streamlit dashboard · read-only (ADR 0002)"]
        RM["read models<br/>+ widen quote to its paragraph"]
        VIEWS["10 pages<br/>Overview · Meta-Analysis · Gap Cards · Limitations ·<br/>Retrieval · Explainer · Comparison · Chat · Verify · Needs Attention"]
        RM --> VIEWS
    end

    USER(("Researcher"))
    J[/"judgments/<br/>verdicts · reviews · fixes · chat · journal"/]

    IN --> ING
    PDF --> PAR
    PIPE == "reads every artifact" ==> RM
    VIEWS ==> USER
    USER -- "accept · reject · review · paste fix" --> J
    J --> RM
    J -. "apply-parse-corrections (CLI)" .-> PD

    LEGEND["‹LLM› = Anthropic API, cached<br/>‹API› = OpenAlex / PubMed<br/>(ingest --resolve also uses ‹API›)"]

    classDef artifact fill:#eef6ff,stroke:#3b82f6;
    classDef stage fill:#fff7e6,stroke:#d97706;
    classDef gate fill:#fdecec,stroke:#dc2626;
    classDef user fill:#ecfdf5,stroke:#059669;
    classDef legend fill:#f8f8f8,stroke:#bbb,stroke-dasharray: 3 3;
    class MAN,PD,PR,EX,EXPJ,NF,CG,RG,J,DROP artifact;
    class ING,PAR,EXR,EXP,AGG,DET,RET stage;
    class GATE gate;
    class USER user;
    class LEGEND legend;
```

</details>

**What the diagram shows**

- Every arrow between stages passes through a JSON file on disk. The disk is
  the state; nothing is kept in a database or a long-running process.
- The Evidence passage is the thread that runs the whole way through. It is
  quoted at **extract**, checked at the gate, carried through **aggregate** and
  **detect**, and widened back to its paragraph when the dashboard shows it.
- Papers drop out at three points: parse failure, zero verified facts, and
  dropped facts. Each one is written down (`parse-report.json`, the
  `unverified` list) and shown on Needs Attention, so nothing is lost silently.
- The dashboard writes only to `judgments/`. Corrections reach the pipeline
  only when someone runs a CLI command.

---

## 2. User experience today

![UX today](diagrams/2-ux-today.png)

<details><summary>Mermaid source</summary>

```mermaid
flowchart TD
    A(["Researcher has a topic"]) --> B["Find papers <b>outside the tool</b><br/>download PDFs, export .bib"]
    B --> C["Create corpora/&lt;name&gt;/<br/>copy PDFs + .bib by hand"]
    C --> D["Put ANTHROPIC_API_KEY in .env"]
    D --> E["<b>Terminal</b>: run 6–7 commands in order<br/>ingest --resolve → parse → extract → explain<br/>→ aggregate → detect → retrieve"]
    E --> E2{"exit code?"}
    E2 -- "1 failed" --> E
    E2 -- "2 incomplete" --> F
    E2 -- "0" --> F
    F["just dashboard"] --> G["Pick Corpus in sidebar"]
    G --> H["Incompleteness banner<br/>(if Papers fell out)"]
    H --> I["<b>10 flat pages</b>, all equally weighted, no suggested order"]

    I --> P1["Corpus Overview"]
    I --> P2["Field Meta-Analysis"]
    I --> P3["Gap Cards<br/>accept / reject"]
    I --> P4["Unanswered Limitations"]
    I --> P5["Retrieval Gaps"]
    I --> P6["Paper Explainer"]
    I --> P7["Paper Comparison"]
    I --> P8["Conversational Analytics<br/>+ narrative summary"]
    I --> P9["Verify Extractions"]
    I --> P10["Needs Attention<br/>paste corrected text"]

    P2 -- "absent edge →" --> P3
    P3 -- "discuss →" --> P8
    P10 --> X["<b>Back to terminal</b><br/>apply-parse-corrections → extract<br/>→ aggregate → detect"]
    X --> F
    P5 -. "add the missing Papers?<br/>(manual: download, re-ingest, re-run all)" .-> B

    classDef pain fill:#fdecec,stroke:#dc2626;
    class B,C,E,X,I pain;
```

</details>

**Where it hurts** (red nodes)

| # | Pain point | Why it matters |
|---|---|---|
| 1 | Finding papers and building the corpus happen outside the tool | The scope ("what was searched") is never recorded, but the Brief requires the dashboard to state it. |
| 2 | The pipeline only runs from the terminal | Researchers are not CLI users, and the dashboard can only say "run `detect` first" (`HANDOFF.md`). |
| 3 | 10 pages with the same weight and no order | Nothing separates "check the corpus first" from "explore" and "write up". Verify and Needs Attention sit at the bottom, though they decide whether the rest can be trusted. |
| 4 | Fixing anything sends you back to the terminal | A pasted correction does nothing until someone runs three CLI commands and reloads. |
| 5 | Retrieval Gaps lead nowhere | The tool suggests missing Papers, but adding one means restarting the whole loop by hand. |
| 6 | Accepting a gap has no effect later on | Verdicts are saved, but the narrative summary and later visits don't build on them in an obvious way. |

---

## 3. User experience as it should be (proposal)

The idea: one guided path that follows a researcher's real task (*scope → trust →
explore → judge → write*). The specialist pages stay, but as tools you reach
from a gap or a Paper, not as the main navigation.

![UX target](diagrams/3-ux-target.png)

<details><summary>Mermaid source</summary>

```mermaid
flowchart TD
    S0(["Researcher has a question"]) --> S1

    subgraph ST1["1 · Scope"]
        S1["New analysis: name, question, field<br/>(the scope statement is written here)"]
        S1 --> S2["Add Papers <b>in the app</b><br/>upload PDFs + .bib / DOI list<br/><i>(open: scholarly-API search, BRIEF item 2)</i>"]
        S2 --> S2b["Pairing check: unmatched entries,<br/>orphan PDFs, the 10–75 rule, fixed in place"]
    end

    subgraph ST2["2 · Build"]
        S3["Pipeline status panel<br/>stage-by-stage progress, cost and cache hits<br/><i>(needs ADR: subprocess runner, HANDOFF.md)</i>"]
    end

    subgraph ST3["3 · Trust"]
        S4["Corpus health = the denominator<br/>N Papers → parsed → sectioned → extracted"]
        S4 --> S5{"Anything fell out?"}
        S5 -- yes --> S6["Fix inline: paste text, review extraction<br/>→ 're-run affected stages' button"]
        S6 --> S3
    end

    subgraph ST4["4 · Explore"]
        S7["Landing map: Coverage Matrix + Meta-Analysis<br/>sparse cells highlighted"]
        S7 --> S8["Ranked gap list<br/>by type, confidence, cell count"]
    end

    subgraph ST5["5 · Judge (per gap)"]
        S8 --> S9["Gap card with Evidence <b>inline</b>"]
        S9 --> T1["Explain a Paper"]
        S9 --> T2["Compare the Papers behind it"]
        S9 --> T3["Discuss in chat"]
        T1 & T2 & T3 --> S10
        S9 --> S10{"Accept / reject<br/>+ note to journal"}
        S10 --> S8
    end

    subgraph ST6["6 · Grow (optional)"]
        R1["Retrieval Gaps: 'likely missing Papers'"]
        R1 --> R2["Add to Corpus → incremental run"]
    end

    subgraph ST7["7 · Write"]
        W1["Narrative summary built from <b>accepted</b> gaps<br/>cites only Corpus Papers"]
        W1 --> W2["Export: Markdown / BibTeX of cited Papers<br/>+ scope & limits statement"]
    end

    S2b --> S3
    S3 --> S4
    S5 -- "no / accepted" --> S7
    S7 -.-> R1
    R2 --> S3
    S10 -- "done triaging" --> W1
    W2 --> RET(["Return weeks later: resume<br/>from journal and saved verdicts"])
    RET --> S8

    classDef adr fill:#fff7e6,stroke:#d97706,stroke-dasharray: 4 3;
    classDef core fill:#ecfdf5,stroke:#059669;
    class S2,S3,S6,R2 adr;
    class S4,S9,S10,W1 core;
```

</details>

**Today vs. proposed**

| Step | Today | Should be | Blocker / decision needed |
|---|---|---|---|
| Scope | Not recorded | Written at the start, shown on every page | Small; fits the Brief's "states its own scope" rule |
| Add Papers | Copy files by hand | Upload in the app | Writing to `papers/` from the UI: ADR 0002 refinement |
| Build | Terminal, 6–7 commands | Status panel with run buttons | **New ADR**: subprocess runner keeps the "imports no pipeline" rule (see `HANDOFF.md`) |
| Trust | Banner + a page at the bottom of the nav | Required checkpoint before exploring, showing the denominator | None: reorders existing pages |
| Explore | 10 flat pages | Map → ranked list | None: navigation only |
| Judge | Gap Cards + cross-page jumps | One card that opens Explainer, Comparison and Chat in context | None |
| Grow | Dead end | Add a suggested Paper → incremental run | Needs the runner ADR + ingest from the UI |
| Write | Summary covers all gaps | Summary covers **accepted** gaps; export | Small: read `judgments/` in `analytics.py` |
| Return | Verdicts persist, no resume point | Resume from journal | Small |

**Navigation decided (prototype verdict, 2026-10-04):** a home screen with a
phase-grouped sidebar (prototype variant B), plus a step bar on every page,
numbered sidebar items, Previous/Next buttons, a status-driven "Recommended
next step", and a **soft Trust gate** (later steps unlock once fallout is fixed
or the denominator is acknowledged). Full verdict: `prototypes/VERDICT.md` on
the `prototype/guided-flow` branch.

Green nodes already exist and only need to move into the main path. Dashed
amber nodes cross the ADR 0002/0006 read-only boundary and need an ADR
decision first (`/skill:domain-modeling`).
