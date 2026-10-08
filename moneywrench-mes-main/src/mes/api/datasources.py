"""Data-source catalogue for the /data page.

Column descriptions + the KPIs each column influences, joined with live
schema introspection and row counts from the database.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mes.db import models as m

# table -> (icon, purpose, {column: (description, influences)})
TABLES: dict[str, dict] = {
    "machines": {
        "icon": "🏭", "model": m.Machine,
        "purpose": "The equipment register — every KPI is ultimately grouped by machine.",
        "columns": {
            "code / name": ("Machine identifier and label",
                            "Grouping key for OEE, downtime, money leaks, energy"),
            "work_center": ("Area of the plant (machining, forming, …)",
                            "Roll-ups per work centre"),
        },
    },
    "products": {
        "icon": "📦", "model": m.Product,
        "purpose": "Product master — holds the ideal cycle time that anchors Performance.",
        "columns": {
            "ideal_cycle_time_sec": ("Rated seconds per unit at nameplate speed",
                                     "Performance = ideal time / run time; speed-loss $"),
            "unit_material_cost": ("Material cost per unit",
                                   "Scrap cost, COPQ, inventory value, COGS"),
            "unit_price": ("Sale price per unit", "ROI and revenue projections"),
        },
    },
    "cost_rates": {
        "icon": "💲", "model": m.CostRate,
        "purpose": "Fully-burdened machine-hour rates — the multiplier that turns minutes into money.",
        "columns": {
            "hourly_rate": ("$ per machine-hour (labour + overhead + energy)",
                            "Every $ figure in money leaks, COPQ, downtime cost"),
        },
    },
    "operators": {
        "icon": "👷", "model": m.Operator,
        "purpose": "Workforce register with shift and skill level.",
        "columns": {
            "shift": ("DAY / SWING", "Workforce productivity per shift"),
            "skill_level": ("1 (trainee) … 5 (expert)",
                            "Correlates with scrap — the quality warden checks this"),
            "hourly_rate": ("Wage", "Labour cost per unit"),
        },
    },
    "work_orders": {
        "icon": "📋", "model": m.WorkOrder,
        "purpose": "The heart of the MES — one row per scheduled production order, plan vs actual.",
        "columns": {
            "scheduled_start / scheduled_end": ("The plan from ERP",
                                                "Schedule adherence, OTD, overrun"),
            "actual_start / actual_end": ("What really happened",
                                          "Availability window, utilization, TEEP"),
            "planned_qty": ("Ordered quantity", "Fulfilment vs produced"),
            "batch_number": ("Genealogy / lot identifier",
                             "Traceability & compliance coverage"),
            "operator_id": ("Who ran the order", "Workforce productivity, scrap by operator"),
        },
    },
    "production_runs": {
        "icon": "▶️", "model": m.ProductionRun,
        "purpose": "Execution record of each order: what was actually produced, when.",
        "columns": {
            "start / end": ("Run window", "Run time for Availability & Performance"),
            "total_count": ("Units produced", "Performance, throughput, quality base"),
        },
    },
    "quality_records": {
        "icon": "🧪", "model": m.QualityRecord,
        "purpose": "Inspection outcome per run — the Quality gear of OEE.",
        "columns": {
            "good_count / scrap_count / rework_count": ("Unit disposition",
                                                        "Quality, FPY, scrap rate, ppm, COPQ"),
            "defect_reason": ("Defect code when scrap occurred",
                              "Quality Pareto drill-downs"),
        },
    },
    "downtime_events": {
        "icon": "🛑", "model": m.DowntimeEvent,
        "purpose": "Every stop, with reason code and Six-Big-Losses category.",
        "columns": {
            "reason_code / description": ("Why the machine stopped",
                                          "Downtime Pareto, diagnosis agent"),
            "category": ("breakdown | changeover | minor_stop | planned_maintenance",
                         "Availability calc; MTBF/MTTR uses breakdowns"),
            "duration_min": ("Minutes lost", "Availability, downtime $, MTTR"),
        },
    },
    "energy_readings": {
        "icon": "⚡", "model": m.EnergyReading,
        "purpose": "kWh metered per work order (running draw + idle draw).",
        "columns": {
            "kwh": ("Energy consumed", "Energy per unit — the deck's efficiency KPI"),
            "cost": ("kWh × tariff ($0.18)", "Energy spend, energy-hog ranking"),
        },
    },
    "inventory_snapshots": {
        "icon": "🗃️", "model": m.InventorySnapshot,
        "purpose": "Month-end stock per product for the inventory subsystem.",
        "columns": {
            "qty_on_hand / unit_cost": ("Stock level and valuation",
                                        "Inventory turns, days of inventory, carrying cost"),
        },
    },
}

SOURCES = [
    {
        "name": "Synthetic plant generator", "icon": "🎲", "kind": "built-in",
        "cite": "src/mes/ingest/synthetic.py — deterministic (seed 42)",
        "link": None,
        "what": "12 months, 6 machines, 5 products, 8 operators, ~6,500 work orders with "
                "planted stories: CNC-01's degrading bearings, PACK-01's speed loss, "
                "ASM-01's valve-body scrap, PRESS-01's changeovers & energy-hungry drive, "
                "GRS-30 overstock, OP-07 the trainee.",
        "influences": "Fills every table above; the default dataset so the whole app works "
                      "out of the box.",
        "command": "python scripts/seed.py",
    },
    {
        "name": "AI4I 2020 Predictive Maintenance (UCI)", "icon": "🎓", "kind": "open data",
        "cite": "S. Matzka, 'AI4I 2020 Predictive Maintenance Dataset', UCI Machine "
                "Learning Repository, 2020. DOI: 10.24432/C5HS5C.",
        "link": "https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset",
        "what": "10,000 machining cycles across three product tiers with five failure "
                "modes (tool wear, heat dissipation, power, overstrain, random).",
        "influences": "Failures → downtime_events (breakdowns) and quality_records (scrap); "
                      "feeds MTBF/MTTR and quality KPIs. No schedules — enrichment data.",
        "command": "python scripts/seed.py --source ai4i --download",
    },
    {
        "name": "Manufacturing Downtime / Line Productivity (Kaggle)", "icon": "📊", "kind": "open data",
        "cite": "A. Pambudi, 'Manufacturing Efficiency in Downtime Operations', Kaggle. "
                "Batches, products, downtime minutes by factor.",
        "link": "https://www.kaggle.com/datasets/agungpambudi/predict-manufacturing-downtime-performance-dataset",
        "what": "Real soda-bottling line: batch productivity, product master with minimum "
                "batch times, downtime minutes per batch by 12 factors.",
        "influences": "Batches → work_orders/production_runs; factors → downtime_events; "
                      "min batch time → ideal cycle. Drives OEE + downtime Pareto.",
        "command": "python scripts/seed.py --source kaggle-downtime --download  (needs Kaggle creds)",
    },
]


def catalogue(session: Session) -> dict:
    tables = []
    for tname, spec in TABLES.items():
        count = session.scalar(select(func.count()).select_from(spec["model"])) or 0
        tables.append({
            "name": tname, "icon": spec["icon"], "purpose": spec["purpose"],
            "rows": count,
            "columns": [{"column": c, "description": d, "influences": i}
                        for c, (d, i) in spec["columns"].items()],
        })
    return {"tables": tables, "sources": SOURCES}
