# 🔧 MoneyWrench MES

*The wrench that pays for itself.*

MoneyWrench MES is a Manufacturing Execution System analytics platform built around one
question: **"Where am I going wrong, and where am I bleeding money?"** It combines a
deterministic KPI engine covering the full MES subsystem matrix with a configurable
multi-agent AI crew (Claude Agent SDK) that interrogates the data and answers in plain
English — with every number traceable to a database column.

---

## Table of contents

1. [Quick start](#1-quick-start)
2. [What's in the app (pages)](#2-whats-in-the-app)
3. [Architecture](#3-architecture)
4. [The KPI engine & formulas](#4-the-kpi-engine--formulas)
5. [Subsystems & the KPI matrix](#5-subsystems--the-kpi-matrix)
6. [The agent crew (configurable models)](#6-the-agent-crew)
7. [Data sources & citations](#7-data-sources--citations)
8. [Project layout](#8-project-layout)
9. [API reference](#9-api-reference)
10. [Testing & verification](#10-testing--verification)

---

## 1. Quick start

Requires Python ≥ 3.10 (the repo uses 3.12 via [uv](https://docs.astral.sh/uv/)).

```bash
cd mes-system
uv sync --extra kaggle          # or: python3.12 -m venv .venv && pip install -e .[kaggle]

# Seed 12 months of synthetic history (6 machines, 5 products, 8 operators)
.venv/bin/python scripts/seed.py

# Run the test suite (KPI formulas verified against hand-computed fixtures)
.venv/bin/python -m pytest

# Start the server
.venv/bin/python -m uvicorn mes.api.main:app --app-dir src --port 8017
```

Then open **http://localhost:8017**. The theme toggle (🌙/☀️) is at the top right;
your choice is remembered per browser.

To ask the agent crew questions from the terminal (needs `ANTHROPIC_API_KEY` or an
active Claude Code login):

```bash
.venv/bin/python scripts/ask.py "where am I bleeding the most money, and why?"
```

## 2. What's in the app

| Page | URL | What it does |
|---|---|---|
| **Dashboard** | `/` | Plant control room: OEE stat cards, monthly trend, A×P×Q per machine, downtime Pareto, money-bleed doughnut, ranked leak table (GUSHING/DRIPPING/DAMP), schedule adherence, and an "Ask the analyst" box wired to the agents. |
| **Subsystems** | `/subsystems` | The **KPI matrix**: seven MES subsystems monitored live, each with status-coded KPI tiles, a drill-down table, and the agent that owns it. |
| **KPI Playground** | `/playground` | Interactive formula lab: drag the Availability/Performance/Quality gears and watch OEE, daily bleed, FPY, ROI and inventory turns react in real time. |
| **The Agents** | `/agents` | Presentation-style tour of the orchestrator and eight specialists — their types, functions, the formulas they enhance, and their *live* configured models. |
| **Sales Advisor** | `/advisor` | The SME-in-a-box for sales executives: profile a client (sector pack, pain points), pick improvement levers, set AI-uplift scenarios, and get ROI/payback with a printable leave-behind and an agent-written pitch. |
| **Data Sources** | `/data` | Every ingestion source with citation, every table, every column — and which KPIs each column influences. Live row counts. |
| **SME chat** | 🧑‍🏭 drawer, every page | Conversational Manufacturing SME: why/how/when of processes and KPIs, grounded in live data, web-search enabled. |
| **API Docs** | `/docs` | Interactive OpenAPI reference for all endpoints. |

## 3. Architecture

```
                        you ("where's my money going?")
                                     │
                              ┌──────▼──────┐        config/agents.yaml
                              │ Orchestrator│◀───────(models swappable)
                              └──────┬──────┘
        ┌───────┬────────┬───────┬──┴───┬────────┬────────┬───────┐
        ▼       ▼        ▼       ▼      ▼        ▼        ▼       ▼
    kpi-    downtime-  cost-  schedule maint.  quality material energy
   analyst investigat. hunter auditor strateg. warden  control. optim.
        └───────┴────────┴───────┴──┬───┴────────┴────────┴───────┘
                                    ▼  (8 KPI tools + read-only SQL, via MCP)
                            ┌───────────────┐
                            │  KPI engine   │  deterministic pandas — the ONLY
                            │ (src/mes/kpi) │  source of numbers in the system
                            └───────┬───────┘
                                    ▼
                            ┌───────────────┐     ┌── synthetic generator
                            │ SQLite (canon │◀────┼── UCI AI4I adapter
                            │  MES schema)  │     └── Kaggle downtime adapter
                            └───────────────┘
```

Design rule: **agents read everything, compute nothing by hand, change nothing.**
The LLM layer decides *where to look* and *how to explain*; the arithmetic is always
the KPI engine's.

This mirrors the three-layer architecture in the Agentic-AI-in-MES concept deck:
an MES layer (orders, work centres, WIP, inventory, quality), an agentic layer
(Planner / Diagnosis / Material / Energy agents on a swappable LLM framework), and
a data-capture layer (here: the ingestion adapters standing in for SCADA/HMI).

## 4. The KPI engine & formulas

All aggregation **sums time buckets first, then takes ratios** — never averages
percentages. Time identity enforced by tests: `window = run time + downtime`.

| KPI | Formula | Module |
|---|---|---|
| **Availability** | run time / planned production time (planned maintenance excluded from planned time; breakdowns, changeovers and logged minor stops count against it) | `kpi/oee.py` |
| **Performance** | (ideal cycle time × total count) / run time | `kpi/oee.py` |
| **Quality** | good count / total count | `kpi/oee.py` |
| **OEE** | Availability × Performance × Quality (world-class ≈ 85%) | `kpi/oee.py` |
| **TEEP** | OEE × utilization (vs calendar time) | `kpi/oee.py` |
| **Six Big Losses** | breakdowns, changeovers, minor stops → availability; speed loss → performance; scrap → quality | `kpi/losses.py` |
| **Schedule adherence / OTD** | on-time orders / all orders (30-min grace) | `kpi/losses.py` |
| **Money bleed** | loss minutes / 60 × machine $/h + scrap × (material + machine time per unit) | `kpi/money.py` |
| **MTBF / MTTR** | run hours / breakdowns · mean repair minutes | `kpi/subsystems.py` |
| **FPY** | (units − scrap − rework) / units | `kpi/subsystems.py` |
| **COPQ** | scrap × (material + cycle-time machine cost) | `kpi/subsystems.py` |
| **Energy per unit** | kWh / good units | `kpi/subsystems.py` |
| **Inventory turns / DOI** | annualized COGS / avg inventory · 365 / turns | `kpi/subsystems.py` |
| **Labour productivity** | units / labour-hour (per operator, shift) | `kpi/subsystems.py` |
| **Traceability coverage** | % orders with batch number + operator sign-off | `kpi/subsystems.py` |

KPI definitions follow the standard OEE/TPM conventions (Nakajima's Six Big Losses;
see e.g. [OEE.com](https://www.oee.com) by Vorne) and the MESA/ISA-95 view of MES
functional areas.

## 5. Subsystems & the KPI matrix

The `/subsystems` page and `/kpi/subsystems` endpoint implement the deck's
complete *"Key KPIs and Subsystems Across MES"* matrix — 7 domains, 34
subsystem functions, **48 KPIs** (41 computed live from the database; 7 shown
as "not instrumented" tiles that name the data feed which would light them up
— e.g. customer returns need an ERP/CRM feed, water needs utility metering):

| Domain (subsystem functions) | KPIs | Owning agent / deck role |
|---|---|---|
| **Production** — scheduling & dispatching, work order mgmt, execution & tracking, resource mgmt, downtime tracking, performance analysis | OEE, throughput, capacity utilization, cycle time, takt vs actual, lead time, schedule adherence, changeover time, downtime planned/unplanned, MTBF, MTTR | schedule-auditor / Planner |
| **Quality & Compliance** — quality mgmt, SPC & analytics, NC & CAPA, document mgmt, regulatory reporting, traceability & genealogy, batch mgmt | FPY, RFT, scrap, rework, DPMO, complaints/returns\*, CoQ, audit compliance, traceability completeness | quality-warden / Diagnosis |
| **Inventory & Materials** — inventory & material mgmt, warehouse & logistics interface, resource mgmt, batch mgmt, process & recipe mgmt | WIP (Little's law), inventory turnover, stock accuracy\*, raw material availability, material yield, batch utilization | material-controller / Material |
| **Workforce** — workforce mgmt, scheduling & dispatching, document mgmt | operator efficiency, labour productivity, overtime ratio, training/cert compliance, absenteeism\* | kpi-analyst / Coordinator |
| **Maintenance** — maintenance mgmt, downtime tracking, resource mgmt, spares interface | maintenance cost/unit, PM compliance, MTBF, MTTR, predictive accuracy\*, spare parts availability\* | maintenance-strategist / Diagnosis |
| **Energy, Sustainability & Costing** — energy & utility monitoring, environmental monitoring, costing & variance, performance analysis | energy/unit, emissions/unit, waste/unit, water/unit\*, production cost/unit, variance vs standard, ROA\* | energy-optimizer / Energy |
| **Order Fulfilment & Logistics** — scheduling & dispatching, WO mgmt, warehouse & logistics, document mgmt, compliance & reporting | OTD, fulfilment cycle time, order accuracy (OTIF), SLA compliance | schedule-auditor / Planner |

\* = not instrumented yet (needs an external feed, named on the tile).
Each computed tile is status-coded (on target / watch / bleeding) against
industry-typical targets. The **KPI Playground** additionally has an
interactive calculator for every KPI in this matrix — 41 calculators across
the 7 domains, plus the deluxe OEE machine, cascade, FPY, ROI and inventory
cards.

## 6. The agent crew

Built on the **Claude Agent SDK**: a lead orchestrator plus eight specialist
subagents, each owning one subsystem. Tools are exposed through an in-process MCP
server (`src/mes/agents/tools.py`).

### Configurable models — swap or upgrade anytime

Models are **settings, not code** — edit [`config/agents.yaml`](config/agents.yaml):

```yaml
default_model: sonnet
orchestrator: { model: inherit, max_turns: 40 }
agents:
  energy-optimizer: { model: opus,  enabled: true }   # give energy a bigger brain
  quality-warden:   { model: haiku, enabled: true }   # cheap + fast for routine checks
  kpi-analyst:      { model: sonnet, enabled: false } # bench an agent entirely
```

Restart the server (or re-run `scripts/ask.py`) and the new models are live. The
`/agents` page shows the current configuration; `GET /api/agents` returns it as JSON.
`inherit` means "use whatever the Claude runtime defaults to" — useful when new
models ship and you upgrade globally.

### Sales Advisor — the SME in a box

`/advisor` is the internal pre-sales tool: it encodes the manufacturing SME's
knowledge so any account executive can run a credible discovery conversation.

- **Levers** (`src/mes/api/advisor_kb.py`): ~10 improvement levers, each with
  the owning agent, the AI action story, a documented savings formula, and
  three uplift scenarios. The **Expected** scenario is calibrated to the
  Agentic-AI-in-MES deck's Monte-Carlo simulation (+28% average OEE gain,
  +47% stability in worst months, +17% in good months); Conservative and
  Aggressive bracket it, and every lever has a manual override slider.
- **Baselines** prefill from the live demo plant and are overwritten with the
  client's rough numbers during the conversation.
- **Double-count guard**: the OEE lever and its component levers (downtime,
  changeover, scrap) are mutually exclusive.
- **Deliverables**: a print-ready leave-behind (`window.print`, print
  stylesheet in `base.html`/`advisor.html`) and an executive pitch written
  live by the `sales-engineer` agent via `POST /analyze` — grounded only in
  the numbers selected on screen.
- **Sector packs**: manufacturing ships live; a new sector = a new `LEVERS`
  dict (logistics, pharma, utilities are stubbed in the dropdown to show the
  extension path).

### Manufacturing SME chat

The 🧑‍🏭 drawer (bottom-right, every page) is a multi-turn conversational SME
for sales executives, built on the Agent SDK's `ClaudeSDKClient` with
per-browser sessions (`src/mes/agents/sme.py`, 30-min idle TTL).

- **Grounding / "deterministic but can pivot"**: current Claude models expose
  no temperature parameter, so accuracy is enforced by protocol — numbers
  must come from the KPI tools or a cited web source; unverifiable claims get
  "I'd need to verify that"; the agent follows user redirections but answers
  contradictions with data. Toggle via `strictness: high|relaxed` on the
  `manufacturing-sme` entry in `config/agents.yaml`; the model is swappable
  like every other agent.
- **Capabilities**: live KPI tools, delegation to the specialist crew, and
  `WebSearch` for standards/benchmarks (ISA-95, SMED, sector norms) with
  sources cited. It knows the app's page map and is told which page you're
  viewing, so "explain this page" works.
- **Endpoints**: `POST /sme/chat` (SSE streaming), `DELETE /sme/chat/{id}`.

## 7. Data sources & citations

The `/data` page documents this interactively with live row counts.

**Bundled synthetic generator** (`src/mes/ingest/synthetic.py`) — deterministic
(seed 42), 12 months × 6 machines × 5 products × 8 operators with planted,
findable problems: CNC-01's degrading spindle bearings, PACK-01's chronic speed
loss, ASM-01's valve-body scrap spike, PRESS-01's long changeovers and inefficient
drive, GRS-30 overstock, and OP-07 the trainee's scrap rate.

**Open datasets** (loaded via adapters in `src/mes/ingest/adapters/`):

> S. Matzka, *AI4I 2020 Predictive Maintenance Dataset*, UCI Machine Learning
> Repository, 2020. DOI: [10.24432/C5HS5C](https://doi.org/10.24432/C5HS5C).
> — `python scripts/seed.py --source ai4i --download` (no account needed)

> A. Pambudi, *Manufacturing Efficiency in Downtime Operations* (line
> productivity, products, downtime factors), Kaggle.
> [kaggle.com/datasets/agungpambudi/predict-manufacturing-downtime-performance-dataset](https://www.kaggle.com/datasets/agungpambudi/predict-manufacturing-downtime-performance-dataset)
> — `python scripts/seed.py --source kaggle-downtime --download` (needs Kaggle credentials)

Any other source (ERP export, SCADA historian, CSV) can be integrated by writing a
small adapter that maps into the canonical schema — see the existing adapters as
templates.

## 8. Project layout

```
mes-system/
├── config/agents.yaml          # ← agent models (swappable)
├── data/mes.db                 # SQLite (generated; gitignored)
├── scripts/
│   ├── seed.py                 # create + load the database
│   └── ask.py                  # CLI access to the agent crew
├── src/mes/
│   ├── db/                     # canonical schema (SQLAlchemy) + session
│   ├── ingest/                 # synthetic generator, adapters, downloaders
│   ├── kpi/                    # frames, oee, losses, money, subsystems
│   ├── agents/                 # tools (MCP), definitions, config, orchestrator
│   ├── api/                    # FastAPI app + data-source catalogue
│   └── web/templates/          # base (theme/sidebar) + 5 pages
└── tests/                      # hand-computed KPI fixtures + generator checks
```

## 9. API reference

Interactive docs at `/docs`. Highlights:

| Endpoint | Returns |
|---|---|
| `GET /kpi/oee?group_by=machine,month` | OEE + components (also `plant` pseudo-group) |
| `GET /kpi/losses` · `/kpi/downtime-pareto` | Six Big Losses, Pareto with cumulative % |
| `GET /kpi/schedule-adherence` | on-time %, delays, overruns |
| `GET /kpi/money-leaks?totals=true` | ranked $ losses (the headline feature) |
| `GET /kpi/subsystems` | the full subsystem KPI matrix |
| `GET /api/agents` | agent crew with live configured models |
| `POST /analyze {"question": "..."}` | multi-agent answer (needs Claude auth) |
| `POST /ingest/synthetic?months=12` | regenerate the database |

## 10. Testing & verification

```bash
.venv/bin/python -m pytest        # 9 tests
```

- `tests/test_kpi.py` — every formula checked against a hand-computed single work
  order (OEE 0.7917, breakdown $120, scrap $80…)
- `tests/test_ingest.py` — generator consistency: time identity holds, quality
  counts reconcile, planted stories are present (PACK-01 slowest, GRD-01 best OEE)

---

*MoneyWrench MES v0.2 "Righty-Tighty" — measuring the plant so the plant can't hide.*
