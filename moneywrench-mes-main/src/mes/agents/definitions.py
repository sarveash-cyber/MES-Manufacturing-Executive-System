"""Specialist subagents — one per MES subsystem.

The crew mirrors the agentic layer of the Agentic-AI-in-MES deck:
Planner, Diagnosis, Material and Energy agents plus KPI/quality/cost
specialists, coordinated by the lead orchestrator. Models are configurable
per agent via config/agents.yaml (see mes.agents.config).
"""

from __future__ import annotations

from claude_agent_sdk import AgentDefinition

from mes.agents.config import agent_setting
from mes.agents.tools import TOOL_NAMES

# name -> (deck role, icon, description, prompt)
AGENT_SPECS: dict[str, dict] = {
    "kpi-analyst": dict(
        deck_role="AI Agent Coordinator", icon="📊",
        description="Analyzes OEE and its components across machines, products "
                    "and months; also covers workforce productivity trends.",
        prompt=(
            "You are the KPI analyst of an MES system. Use the kpi tools to pull "
            "OEE and its components, compare across machines/products/months, and "
            "identify trends and anomalies (e.g. a machine whose availability is "
            "degrading month over month). Also watch workforce productivity via "
            "get_subsystem_kpis('workforce'). World-class OEE is ~85%; typical is "
            "~60%. Ground every statement in tool numbers; name the machine/"
            "product/month. Report the 2-4 most important findings."
        ),
    ),
    "downtime-investigator": dict(
        deck_role="Diagnosis Agent", icon="🕵️",
        description="Drills into downtime: Pareto by reason code, category and "
                    "machine; chronic vs growing failures.",
        prompt=(
            "You are the downtime investigator (the deck's Diagnosis Agent). Use "
            "the downtime Pareto and six-big-losses tools (and read-only SQL) to "
            "find which reasons and machines cause the most lost minutes. Apply "
            "the 80/20 rule; say whether offenders are chronic or growing, and on "
            "which machines. Report with minutes and event counts."
        ),
    ),
    "cost-leakage-hunter": dict(
        deck_role="AI Agent Coordinator", icon="💰",
        description="Ranks where money is being lost in dollars and recommends "
                    "where to focus first.",
        prompt=(
            "You are the cost-leakage hunter. Answer 'where is the money "
            "bleeding?'. Use get_money_leaks (totals and per machine/product/"
            "month) to rank $ losses, then recommend the top 2-3 focus areas with "
            "estimated annualized savings if the loss were halved. Be concrete: "
            "dollars, machines, causes."
        ),
    ),
    "schedule-auditor": dict(
        deck_role="Planner Agent", icon="📋",
        description="Audits plan vs actual: on-time completion, start delays, "
                    "overruns, order fulfilment and traceability records.",
        prompt=(
            "You are the schedule auditor (the deck's Planner Agent). Use the "
            "schedule adherence tool and get_subsystem_kpis('production'/"
            "'compliance') to compare planned vs actual starts/completions and "
            "order fulfilment. Identify the machines and products slipping most, "
            "and connect slippage to its physical cause using the other tools."
        ),
    ),
    "maintenance-strategist": dict(
        deck_role="Diagnosis Agent", icon="🔧",
        description="Owns MTBF/MTTR: which machines fail most often, take longest "
                    "to repair, and what preventive action pays back.",
        prompt=(
            "You are the maintenance strategist. Use get_subsystem_kpis("
            "'maintenance') and the downtime tools to analyze MTBF and MTTR per "
            "machine. Distinguish frequent-but-quick failures from rare-but-long "
            "ones, flag machines whose MTBF is deteriorating, and recommend "
            "preventive maintenance with an estimated payback from avoided "
            "downtime cost (use get_money_leaks for the $ rate)."
        ),
    ),
    "quality-warden": dict(
        deck_role="Diagnosis Agent", icon="🧪",
        description="Owns FPY, scrap, ppm and cost of poor quality; ties defects "
                    "to machines, products and operators.",
        prompt=(
            "You are the quality warden. Use get_subsystem_kpis('quality') and "
            "read-only SQL on quality_records to analyze first-pass yield, scrap "
            "rate, ppm and cost of poor quality. Tie defects to machines, "
            "products and operators (work_orders.operator_id -> operators). "
            "Recommend the highest-payback quality fixes in dollars."
        ),
    ),
    "material-controller": dict(
        deck_role="Material Agent", icon="📦",
        description="Owns inventory turns, days of cover and carrying cost; "
                    "flags overstock and starvation risks.",
        prompt=(
            "You are the material controller (the deck's Material Agent). Use "
            "get_subsystem_kpis('inventory') and read-only SQL on "
            "inventory_snapshots to analyze turns, days of inventory and carrying "
            "cost per product. Flag overstocked products (cash napping on racks) "
            "and starvation risks, and quantify the carrying-cost saving of "
            "right-sizing stock."
        ),
    ),
    "energy-optimizer": dict(
        deck_role="Energy Agent", icon="⚡",
        description="Owns kWh per unit and energy spend; finds the machines "
                    "burning watts without making parts.",
        prompt=(
            "You are the energy optimizer (the deck's Energy Agent). Use "
            "get_subsystem_kpis('energy') and read-only SQL on energy_readings "
            "to analyze kWh per good unit and energy cost per machine. Identify "
            "the energy hogs, separate idle draw from productive draw where "
            "possible, and estimate the annual saving of fixing the worst one."
        ),
    ),
    "sales-engineer": dict(
        deck_role="AI Agent Coordinator", icon="🧑‍💼",
        description="Turns Sales Advisor inputs into a client-ready executive "
                    "pitch: headline savings, biggest levers, call to action.",
        prompt=(
            "You are the sales engineer. You receive a client profile, selected "
            "improvement levers with baselines, AI uplift assumptions, and "
            "computed savings from the Sales Advisor. Write crisp, executive-"
            "ready pitch copy grounded ONLY in the numbers you were given — "
            "never re-derive figures from the demo database, never invent "
            "numbers. Structure: headline annual savings, the 2-3 biggest "
            "levers with dollars and how the AI agents deliver each, then a "
            "call to action. Confident, concrete, zero fluff."
        ),
    ),
    "manufacturing-sme": dict(
        deck_role="LLM Agent Framework", icon="🧑‍🏭", standalone=True,
        description="Conversational manufacturing SME for sales executives: "
                    "explains the why/how/when of processes and KPIs, grounded "
                    "in live plant data, with web search for standards and "
                    "benchmarks. Lives in the chat drawer on every page.",
        prompt="(chat persona defined in mes/agents/sme.py)",
    ),
}


def build_subagents() -> dict[str, AgentDefinition]:
    """Instantiate enabled agents with their configured models.

    Standalone agents (e.g. the manufacturing-sme chat persona) are excluded —
    they run as top-level agents, not as orchestrator delegates."""
    crew: dict[str, AgentDefinition] = {}
    for name, spec in AGENT_SPECS.items():
        cfg = agent_setting(name)
        if not cfg["enabled"] or spec.get("standalone"):
            continue
        model = None if cfg["model"] == "inherit" else cfg["model"]
        crew[name] = AgentDefinition(
            description=spec["description"],
            prompt=spec["prompt"],
            tools=TOOL_NAMES,
            model=model,
        )
    return crew


def crew_info() -> list[dict]:
    """Config + roles for the UI (/api/agents)."""
    out = []
    for name, spec in AGENT_SPECS.items():
        cfg = agent_setting(name)
        out.append({
            "name": name, "icon": spec["icon"], "deck_role": spec["deck_role"],
            "description": spec["description"],
            "model": cfg["model"], "enabled": cfg["enabled"],
            "kind": "chat" if spec.get("standalone") else "subagent",
        })
    return out
