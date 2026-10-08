"""Sales Advisor knowledge base — the SME-in-a-box brain.

LEVERS: the improvement levers a sales executive can select for a client,
each mapping a pain point to a KPI, the AI agent that works it, uplift
scenarios (the "Expected" numbers come from the Agentic-AI-in-MES deck's
Monte-Carlo simulation), and a documented savings formula evaluated
client-side in advisor.html.

`advisor_payload()` joins this static knowledge with live baselines computed
from the demo plant (reusing the KPI frames — same arithmetic as
kpi/subsystems.py) so the wizard is prefilled and the demo never starts
empty.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from mes.db import models as m
from mes.kpi.frames import downtime_frame, work_order_frame
from mes.kpi.losses import ON_TIME_GRACE_MIN
from mes.kpi.subsystems import CARRY_RATE

WORKING_DAYS = 330
MC_BASIS = ("Monte-Carlo simulation from the Agentic-AI-in-MES deck (Latin "
            "Hypercube sampling): +28% average OEE gain with the AI agent, "
            "+47% stability in worst months, +17% in good months")

PAIN_POINTS = {
    "downtime":  {"label": "Machines down too often", "icon": "🛑"},
    "quality":   {"label": "Scrap & rework eating margin", "icon": "🧪"},
    "delivery":  {"label": "Late deliveries / broken promises", "icon": "🚚"},
    "inventory": {"label": "Cash tied up in inventory", "icon": "📦"},
    "energy":    {"label": "Energy bills climbing", "icon": "⚡"},
    "labour":    {"label": "Labour costs / skills gap", "icon": "👷"},
    "audit":     {"label": "Audit & compliance burden", "icon": "📋"},
    "throughput": {"label": "Can't make enough product", "icon": "🏭"},
}

# Savings formulas are evaluated in advisor.html; the `formula` strings here
# are the documentation shown to the exec (and printed on the leave-behind).
LEVERS: dict[str, dict] = {
    "oee": {
        "name": "Overall OEE uplift", "domain": "Production", "icon": "⚙️",
        "agent": "kpi-analyst", "deck_agent": "Planner + Diagnosis Agents",
        "pains": ["throughput"],
        "excludes": ["downtime", "changeover", "scrap"],
        "ai_action": "The agent crew reschedules around failures, catches "
                     "degrading availability early and keeps the three OEE "
                     "gears turning — the deck's headline simulation result.",
        "uplift": {"conservative": 0.10, "expected": 0.28, "aggressive": 0.40},
        "uplift_basis": MC_BASIS,
        "formula": "extra good units = capacity × ΔOEE; savings = extra units × margin",
    },
    "downtime": {
        "name": "Unplanned downtime recovered", "domain": "Production", "icon": "🛑",
        "agent": "downtime-investigator", "deck_agent": "Diagnosis Agent",
        "pains": ["downtime", "throughput"], "excludes": ["oee"],
        "ai_action": "Diagnosis agent finds the 20% of reason codes causing "
                     "80% of stops and flags growing failures before they "
                     "become breakdowns.",
        "uplift": {"conservative": 0.15, "expected": 0.30, "aggressive": 0.50},
        "uplift_basis": "share of unplanned hours recovered; Pareto-driven "
                        "fixes typically recover 25–40% (deck worst-month "
                        "stability gain: +47%)",
        "formula": "savings = unplanned h/yr × share recovered × machine $/h",
    },
    "changeover": {
        "name": "Changeover time cut (SMED)", "domain": "Production", "icon": "🔄",
        "agent": "schedule-auditor", "deck_agent": "Planner Agent",
        "pains": ["downtime", "throughput"], "excludes": ["oee"],
        "ai_action": "Planner agent sequences orders to minimise product "
                     "switches and flags changeovers running past standard.",
        "uplift": {"conservative": 0.15, "expected": 0.30, "aggressive": 0.50},
        "uplift_basis": "classic SMED programs halve changeover; AI "
                        "sequencing alone reliably saves 15–30%",
        "formula": "savings = changeover h/yr × share avoided × machine $/h",
    },
    "scrap": {
        "name": "Scrap & rework reduction", "domain": "Quality", "icon": "🧪",
        "agent": "quality-warden", "deck_agent": "Diagnosis Agent",
        "pains": ["quality"], "excludes": ["oee"],
        "ai_action": "Quality warden ties defects to machine × product × "
                     "operator and catches drift before a batch is lost.",
        "uplift": {"conservative": 0.15, "expected": 0.30, "aggressive": 0.50},
        "uplift_basis": "SPC-with-agents programs typically cut scrap 20–40%",
        "formula": "savings = scrap units/yr × share avoided × (material + machine time $/unit)",
    },
    "otd": {
        "name": "On-time delivery rescue", "domain": "Fulfilment", "icon": "🚚",
        "agent": "schedule-auditor", "deck_agent": "Planner Agent",
        "pains": ["delivery"],
        "ai_action": "Planner agent audits plan vs reality daily and "
                     "re-promises before the customer notices — fewer "
                     "expedites, fewer penalties.",
        "uplift": {"conservative": 0.20, "expected": 0.40, "aggressive": 0.60},
        "uplift_basis": "share of late orders eliminated; adaptive "
                        "rescheduling pilots report 30–50%",
        "formula": "savings = late orders/yr × share fixed × cost per late order (expedite/penalty)",
    },
    "inventory": {
        "name": "Inventory right-sizing", "domain": "Materials", "icon": "📦",
        "agent": "material-controller", "deck_agent": "Material Agent",
        "pains": ["inventory"],
        "ai_action": "Material agent watches days-of-cover per product and "
                     "recommends reorder points — cash stops napping on racks.",
        "uplift": {"conservative": 0.10, "expected": 0.20, "aggressive": 0.35},
        "uplift_basis": "inventory value released; demand-driven MRP "
                        "programs report 15–30%",
        "formula": "savings = inventory value × share released × carrying rate (25%/yr)",
    },
    "energy": {
        "name": "Energy per unit cut", "domain": "Energy", "icon": "⚡",
        "agent": "energy-optimizer", "deck_agent": "Energy Agent",
        "pains": ["energy"],
        "ai_action": "Energy agent separates productive draw from idle draw "
                     "and names the machines burning watts without making parts.",
        "uplift": {"conservative": 0.08, "expected": 0.15, "aggressive": 0.25},
        "uplift_basis": "idle-draw elimination + load scheduling typically "
                        "saves 10–20% of plant kWh",
        "formula": "savings = kWh/yr × share saved × tariff $/kWh",
    },
    "labour": {
        "name": "Labour productivity lift", "domain": "Workforce", "icon": "👷",
        "agent": "kpi-analyst", "deck_agent": "AI Agent Coordinator",
        "pains": ["labour"],
        "ai_action": "Agent-written shift handovers, guided work instructions "
                     "and per-operator coaching signals raise output per hour.",
        "uplift": {"conservative": 0.05, "expected": 0.12, "aggressive": 0.20},
        "uplift_basis": "operator-assist deployments report 8–15% output/hour",
        "formula": "savings = labour hours/yr × share saved × loaded wage",
    },
    "maintenance": {
        "name": "Maintenance cost optimisation", "domain": "Maintenance", "icon": "🔧",
        "agent": "maintenance-strategist", "deck_agent": "Diagnosis Agent",
        "pains": ["downtime"],
        "ai_action": "Maintenance strategist shifts spend from breakdowns to "
                     "planned work — cheaper hours, fewer surprises.",
        "uplift": {"conservative": 0.10, "expected": 0.20, "aggressive": 0.30},
        "uplift_basis": "reactive→preventive shifts cut total maintenance "
                        "cost 15–25% (breakdown hours cost 3–5× planned)",
        "formula": "savings = maintenance $/yr × share optimised",
    },
    "audit": {
        "name": "Audit & traceability effort", "domain": "Compliance", "icon": "📋",
        "agent": "schedule-auditor", "deck_agent": "AI Agent Coordinator",
        "pains": ["audit"],
        "ai_action": "Genealogy is queryable in seconds ('trace batch "
                     "VLV-2603') and audit packs assemble themselves from "
                     "the event log.",
        "uplift": {"conservative": 0.30, "expected": 0.50, "aggressive": 0.70},
        "uplift_basis": "share of manual evidence-gathering hours eliminated "
                        "when records are digital and agent-queryable",
        "formula": "savings = audit prep hours/yr × share eliminated × loaded wage",
    },
}

SECTOR_PACKS = [
    {"key": "manufacturing", "label": "Manufacturing (discrete & process)", "live": True},
    {"key": "logistics", "label": "Logistics & warehousing — coming soon", "live": False},
    {"key": "pharma", "label": "Pharma & life sciences — coming soon", "live": False},
    {"key": "utilities", "label": "Energy & utilities — coming soon", "live": False},
]


def advisor_payload(session: Session) -> dict:
    """Static KB + live demo-plant baselines for prefill."""
    wo = work_order_frame(session)
    baselines: dict = {}
    economics: dict = {
        "machines": 6, "hours_per_day": 16, "good_units_per_day": 8000,
        "margin_per_unit": 4.0, "machine_rate": 120.0, "tariff": 0.18,
        "loaded_wage": 42.0, "working_days": WORKING_DAYS,
        "cost_per_late_order": 180.0, "audit_hours_per_year": 400.0,
    }
    if not wo.empty:
        dt = downtime_frame(session)
        days = max(1.0, (wo["actual_end"].max() - wo["actual_start"].min()).days)
        total = wo["total_count"].sum()
        good = wo["good_count"].sum()
        ppt = wo["planned_production_min"].sum()
        run = wo["run_time_min"].sum()
        ideal_min = (wo["ideal_cycle_time_sec"] * wo["total_count"]).sum() / 60
        oee = (run / max(ppt, 1)) * (ideal_min / max(run, 1)) * (good / max(total, 1))
        scrap_units_day = wo["scrap_count"].sum() / days
        scrap_cost_unit = float(
            ((wo["unit_material_cost"] + wo["ideal_cycle_time_sec"] / 3600
              * wo["hourly_rate"]) * wo["scrap_count"]).sum()
            / max(wo["scrap_count"].sum(), 1))
        late_day = (wo["end_delay_min"] > ON_TIME_GRACE_MIN).sum() / days
        brk = dt[dt["category"] == m.CAT_BREAKDOWN] if not dt.empty else dt
        pm = dt[dt["category"] == m.CAT_PLANNED_MAINTENANCE] if not dt.empty else dt
        rate_by_m = wo.groupby("machine")["hourly_rate"].first()
        maint_cost_yr = float(
            ((brk.groupby("machine")["duration_min"].sum() / 60 * rate_by_m).sum()
             if not brk.empty else 0.0)
            + ((pm.groupby("machine")["duration_min"].sum() / 60 * rate_by_m).sum()
               if not pm.empty else 0.0)) * 365 / days
        snaps = pd.read_sql(
            select(m.InventorySnapshot.date, m.InventorySnapshot.qty_on_hand,
                   m.InventorySnapshot.unit_cost), session.get_bind())
        inv_value = float((snaps["qty_on_hand"] * snaps["unit_cost"])
                          .groupby(snaps["date"]).sum().mean()) if not snaps.empty else 250000.0
        economics.update({
            "machines": int(wo["machine"].nunique()),
            "good_units_per_day": round(good / days),
            "machine_rate": round(float(wo["hourly_rate"].mean()), 0),
            "margin_per_unit": round(float(
                (wo["unit_price"] - wo["unit_material_cost"]).mean()), 2),
        })
        baselines = {
            "oee_pct": round(oee * 100, 1),
            "unplanned_h_per_day": round(wo["unplanned_downtime_min"].sum() / 60 / days, 2),
            "changeover_h_per_day": round(wo[m.CAT_CHANGEOVER].sum() / 60 / days, 2),
            "scrap_units_per_day": round(scrap_units_day, 1),
            "scrap_cost_per_unit": round(scrap_cost_unit, 2),
            "late_orders_per_day": round(late_day, 2),
            "inventory_value": round(inv_value),
            "kwh_per_year": round(wo["kwh"].sum() * 365 / days),
            "labour_hours_per_day": round(run / 60 / days, 1),
            "maintenance_cost_per_year": round(maint_cost_yr),
            "carry_rate": CARRY_RATE,
        }
    return {
        "pain_points": PAIN_POINTS,
        "levers": LEVERS,
        "sectors": SECTOR_PACKS,
        "economics": economics,
        "baselines": baselines,
        "mc_basis": MC_BASIS,
    }
