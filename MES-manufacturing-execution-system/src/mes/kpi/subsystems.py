"""Subsystem KPI matrix — the complete 'Key KPIs and Subsystems Across MES'
table from Faizan's Agentic-AI-in-MES deck.

Seven domains, every subsystem function, every KPI from the matrix:

1. Production          OEE, throughput, capacity utilization, cycle time,
                       takt vs actual, lead time, schedule adherence,
                       changeover time, downtime planned/unplanned, MTTR, MTBF
2. Quality &           FPY, RFT, scrap, rework, DPMO, complaints/returns,
   Compliance          CoQ, audit compliance, traceability completeness
3. Inventory &         WIP, inventory turnover, stock accuracy, raw material
   Materials           availability, material yield, batch utilization
4. Workforce           operator efficiency, labour productivity, overtime
                       ratio, training/certification compliance, absenteeism
5. Maintenance         maintenance cost/unit, PM compliance, predictive
                       accuracy, spare parts availability (+ MTBF/MTTR)
6. Energy, Sustain-    energy/unit, water/unit, waste & emissions/unit,
   ability & Costing   production cost/unit, variance vs standard, ROA
7. Order Fulfilment    OTD, order fulfilment cycle time, order accuracy
   & Logistics         (OTIF proxy), SLA compliance

KPIs the schema can't measure yet are reported with status "na" and a note
saying which data source would light them up — monitored honestly, never
invented. Everything else is deterministic pandas over the canonical schema.
"""

from __future__ import annotations

import math

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from mes.db import models as m
from mes.kpi.frames import downtime_frame, work_order_frame
from mes.kpi.losses import ON_TIME_GRACE_MIN

CARRY_RATE = 0.25       # annual inventory carrying cost
GRID_CO2_KG_PER_KWH = 0.4  # emissions factor (grid average)
SHIFT_END_HOUR = 22


def _status(value, good: float, warn: float, higher_is_better: bool = True) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "warn"
    if higher_is_better:
        return "good" if value >= good else "warn" if value >= warn else "bad"
    return "good" if value <= good else "warn" if value <= warn else "bad"


def _kpi(name, value, unit, status, note=""):
    return {"name": name, "value": value, "unit": unit, "status": status, "note": note}


def _na(name, needs: str):
    return {"name": name, "value": "—", "unit": "", "status": "na",
            "note": f"not instrumented — needs {needs}"}


def _rows(df: pd.DataFrame, cols: list[str], n: int = 8) -> dict:
    df = df.head(n).copy()
    for c in df.columns:
        if pd.api.types.is_float_dtype(df[c]):
            df[c] = df[c].round(2)
    return {"columns": cols, "rows": df[cols].to_dict(orient="records")}


def subsystem_matrix(session: Session) -> dict:
    wo = work_order_frame(session)
    if wo.empty:
        return {"subsystems": [], "error": "no data — run scripts/seed.py"}
    dt = downtime_frame(session)
    days = max(1.0, (wo["actual_end"].max() - wo["actual_start"].min()).days)
    n_machines = wo["machine"].nunique()
    total = wo["total_count"].sum()
    good_units = wo["good_count"].sum()
    scrap = wo["scrap_count"].sum()
    rework = wo["rework_count"].sum()
    run_min = wo["run_time_min"].sum()
    ppt_min = wo["planned_production_min"].sum()
    subsystems = []

    # ======================================================================
    # 1. PRODUCTION — scheduling & dispatching, WO mgmt, execution &
    #    tracking, resource mgmt, downtime tracking, performance analysis
    # ======================================================================
    throughput = good_units / days
    calendar_min = days * 1440 * n_machines
    cap_util = ppt_min / calendar_min * 100
    act_cycle = run_min * 60 / max(total, 1)
    ideal_cycle = (wo["ideal_cycle_time_sec"] * wo["total_count"]).sum() / max(total, 1)
    demand_rate = wo["planned_qty"].sum() / days                    # units/day asked of the plant
    takt = ppt_min * 60 / days / max(demand_rate, 1)                # available sec per demanded unit
    lead_h = ((wo["actual_end"] - wo["scheduled_start"])
              .dt.total_seconds() / 3600).mean()
    adherence = (wo["end_delay_min"] <= ON_TIME_GRACE_MIN).mean() * 100
    chg = wo[m.CAT_CHANGEOVER].sum()
    chg_per_order = chg / max(len(wo), 1)
    unplanned = wo["unplanned_downtime_min"].sum()
    planned_dt = wo["planned_downtime_min"].sum()
    brk = dt[dt["category"] == m.CAT_BREAKDOWN] if not dt.empty else dt
    n_brk = len(brk)
    mtbf = (run_min / 60) / max(n_brk, 1)
    mttr = brk["duration_min"].mean() if not brk.empty else 0.0
    oee_plant = ((run_min / max(ppt_min, 1))
                 * ((wo["ideal_cycle_time_sec"] * wo["total_count"]).sum() / 60 / max(run_min, 1))
                 * (good_units / max(total, 1))) * 100
    otd_m = (wo.assign(ot=wo["end_delay_min"] <= ON_TIME_GRACE_MIN)
               .groupby("machine", as_index=False)
               .agg(orders=("wo_id", "count"), on_time_pct=("ot", "mean")))
    otd_m["on_time_pct"] = (otd_m["on_time_pct"] * 100).round(1)
    subsystems.append({
        "key": "production", "name": "Production", "icon": "🏭",
        "agent": "schedule-auditor", "deck_agent": "Planner Agent",
        "functions": ["Production Scheduling & Dispatching", "Work Order Management",
                      "Production Execution & Tracking", "Resource Management",
                      "Downtime Tracking & Analysis", "Performance Analysis"],
        "blurb": "Orders from ERP → dispatched, executed, tracked. The engine room of every other KPI.",
        "kpis": [
            _kpi("OEE (A×P×Q)", round(oee_plant, 1), "%", _status(oee_plant, 85, 60), "world-class ≥ 85%"),
            _kpi("Throughput", round(throughput), "units/day", "good", f"{int(good_units):,} good units"),
            _kpi("Capacity utilization", round(cap_util, 1), "%", _status(cap_util, 70, 45),
                 "planned production / calendar time"),
            _kpi("Cycle time", round(act_cycle, 1), "sec/unit",
                 _status(act_cycle / max(ideal_cycle, .1), 1.1, 1.3, False), f"ideal {ideal_cycle:.1f}s"),
            _kpi("Takt vs actual", f"{takt:.1f} / {act_cycle:.1f}", "sec",
                 _status(act_cycle / max(takt, .1), 1.0, 1.15, False),
                 "takt = available time / demand"),
            _kpi("Lead time", round(lead_h, 1), "h", _status(lead_h, 12, 24, False),
                 "schedule → completion"),
            _kpi("Schedule adherence", round(adherence, 1), "%", _status(adherence, 90, 70),
                 f"{ON_TIME_GRACE_MIN:.0f} min grace"),
            _kpi("Changeover time", round(chg_per_order, 1), "min/order",
                 _status(chg_per_order, 15, 30, False), f"{chg/60:,.0f} h total"),
            _kpi("Downtime P/U", f"{planned_dt/60:,.0f} / {unplanned/60:,.0f}", "h",
                 _status(unplanned / max(ppt_min, 1), .08, .15, False), "planned / unplanned"),
            _kpi("MTBF", round(mtbf, 1), "run-h", _status(mtbf, 100, 50), "between breakdowns"),
            _kpi("MTTR", round(float(mttr), 1), "min", _status(float(mttr), 60, 100, False), "mean repair"),
        ],
        "table": _rows(otd_m.sort_values("on_time_pct"), ["machine", "orders", "on_time_pct"]),
    })

    # ======================================================================
    # 2. QUALITY & COMPLIANCE — quality mgmt, SPC & analytics, NC & CAPA,
    #    document mgmt, compliance & regulatory, traceability, batch mgmt
    # ======================================================================
    fpy = (total - scrap - rework) / max(total, 1) * 100
    rft = (total - scrap) / max(total, 1) * 100
    scrap_rate = scrap / max(total, 1) * 100
    rework_rate = rework / max(total, 1) * 100
    dpmo = scrap / max(total, 1) * 1e6   # one opportunity per unit
    copq = (wo["scrap_count"] * (wo["unit_material_cost"]
            + wo["ideal_cycle_time_sec"] / 3600 * wo["hourly_rate"])).sum()
    genealogy = wo["batch_number"].notna().mean() * 100
    audit_ready = (wo["batch_number"].notna() & wo["operator"].notna()).mean() * 100
    q_m = (wo.groupby("machine", as_index=False)
             .agg(units=("total_count", "sum"), scrap=("scrap_count", "sum")))
    q_m["scrap_pct"] = (q_m["scrap"] / q_m["units"] * 100).round(2)
    subsystems.append({
        "key": "quality", "name": "Quality & Compliance", "icon": "🧪",
        "agent": "quality-warden", "deck_agent": "Diagnosis Agent",
        "functions": ["Quality Management", "SPC & Analytics", "Nonconformance & CAPA",
                      "Document Management", "Compliance & Regulatory Reporting",
                      "Traceability & Genealogy", "Batch Management"],
        "blurb": "Every reject priced, every batch traceable — what auditors and accountants both want.",
        "kpis": [
            _kpi("First Pass Yield", round(fpy, 2), "%", _status(fpy, 97, 93), "no scrap, no rework"),
            _kpi("Right First Time", round(rft, 2), "%", _status(rft, 97, 93), "incl. reworked units"),
            _kpi("Scrap rate", round(scrap_rate, 2), "%", _status(scrap_rate, 1.5, 4, False),
                 f"{int(scrap):,} units"),
            _kpi("Rework rate", round(rework_rate, 2), "%", _status(rework_rate, 1, 3, False),
                 f"{int(rework):,} units"),
            _kpi("DPMO", round(dpmo), "", _status(dpmo, 15000, 40000, False),
                 "defects per million opportunities"),
            _kpi("Cost of poor quality", round(copq), "$", _status(copq / days, 300, 800, False),
                 f"${copq/days:,.0f}/day"),
            _kpi("Audit compliance", round(audit_ready, 1), "%", _status(audit_ready, 99, 90),
                 "batch + operator + quality record"),
            _kpi("Traceability completeness", round(genealogy, 1), "%", _status(genealogy, 99, 90),
                 "orders with batch genealogy"),
            _na("Complaints / returns", "customer returns feed (ERP/CRM)"),
        ],
        "table": _rows(q_m.sort_values("scrap_pct", ascending=False),
                       ["machine", "units", "scrap", "scrap_pct"]),
    })

    # ======================================================================
    # 3. INVENTORY & MATERIALS — inventory & material mgmt, warehouse &
    #    logistics interface, resource mgmt, batch mgmt, process & recipe
    # ======================================================================
    snaps = pd.read_sql(select(m.InventorySnapshot.date, m.InventorySnapshot.qty_on_hand,
                               m.InventorySnapshot.unit_cost,
                               m.Product.code.label("product"))
                        .join(m.Product, m.InventorySnapshot.product_id == m.Product.id),
                        session.get_bind())
    kpis, table = [], {"columns": [], "rows": []}
    wip_units = throughput * lead_h / 24            # Little's law: WIP = rate × lead time
    mat_wait = dt[dt["reason_code"] == "MAT-WAIT"]["duration_min"].sum() if not dt.empty else 0
    raw_avail = (1 - mat_wait / max(ppt_min, 1)) * 100
    mat_yield = good_units / max(total, 1) * 100
    batch_util = (wo["total_count"] / wo["planned_qty"].clip(lower=1)).mean() * 100
    kpis += [
        _kpi("WIP level", round(wip_units), "units", _status(wip_units / max(throughput, 1), 1.0, 2.0, False),
             "Little's law: throughput × lead time"),
        _kpi("Raw material availability", round(raw_avail, 2), "%", _status(raw_avail, 99, 97),
             "1 − material-wait / planned time"),
        _kpi("Material yield", round(mat_yield, 2), "%", _status(mat_yield, 98, 95),
             "good units / units started"),
        _kpi("Batch utilization", round(batch_util, 1), "%", _status(batch_util, 98, 90),
             "actual vs planned batch size"),
        _na("Stock accuracy", "cycle-count records (WMS)"),
    ]
    if not snaps.empty:
        snaps["value"] = snaps["qty_on_hand"] * snaps["unit_cost"]
        by_month = snaps.groupby("date")["value"].sum()
        avg_inv = by_month.mean()
        cogs_yr = (wo["total_count"] * wo["unit_material_cost"]).sum() * 365 / days
        turns = cogs_yr / max(avg_inv, 1)
        doi = 365 / max(turns, 0.001)
        kpis.insert(1, _kpi("Inventory turnover", round(turns, 1), "×/yr", _status(turns, 8, 4),
                            f"DOI {doi:.0f} days · carrying ${avg_inv*CARRY_RATE:,.0f}/yr"))
        latest = snaps[snaps["date"] == snaps["date"].max()]
        inv_p = (latest.groupby("product", as_index=False)
                       .agg(qty=("qty_on_hand", "sum"), value=("value", "sum")))
        cons = (wo.groupby("product")["total_count"].sum() / days).rename("daily_use")
        inv_p = inv_p.merge(cons, left_on="product", right_index=True, how="left")
        inv_p["days_cover"] = (inv_p["qty"] / inv_p["daily_use"].clip(lower=0.1)).round(1)
        table = _rows(inv_p.sort_values("days_cover", ascending=False),
                      ["product", "qty", "value", "days_cover"])
    subsystems.append({
        "key": "inventory", "name": "Inventory & Materials", "icon": "📦",
        "agent": "material-controller", "deck_agent": "Material Agent",
        "functions": ["Inventory & Material Management", "Warehouse & Logistics Interface",
                      "Resource Management", "Batch Management", "Process & Recipe Management"],
        "blurb": "Stock, WIP and material flow — cash in hi-vis vests, some of it asleep.",
        "kpis": kpis, "table": table,
    })

    # ======================================================================
    # 4. WORKFORCE — workforce mgmt, scheduling & dispatching, document mgmt
    # ======================================================================
    if wo["operator"].notna().any():
        w = wo.dropna(subset=["operator"])
        ops = (w.groupby(["operator", "operator_shift", "operator_skill"], as_index=False)
                .agg(orders=("wo_id", "count"), units=("total_count", "sum"),
                     scrap=("scrap_count", "sum"), run_min=("run_time_min", "sum"),
                     ideal_min=("ideal_time_min", "sum")))
        ops["units_per_hour"] = (ops["units"] / (ops["run_min"] / 60).clip(lower=0.1)).round(1)
        ops["efficiency_pct"] = (ops["ideal_min"] / ops["run_min"].clip(lower=0.1) * 100).round(1)
        ops["scrap_pct"] = (ops["scrap"] / ops["units"] * 100).round(2)
        uplh = w["total_count"].sum() / max(w["run_time_min"].sum() / 60, 1)
        op_eff = (w["ideal_time_min"].sum() / max(w["run_time_min"].sum(), 1)) * 100
        after_hours = ((w["actual_end"].dt.hour >= SHIFT_END_HOUR)
                       | (w["actual_end"].dt.hour < 6)).mean() * 100
        certified = (w["operator_skill"] >= 3).mean() * 100
        subsystems.append({
            "key": "workforce", "name": "Workforce", "icon": "👷",
            "agent": "kpi-analyst", "deck_agent": "AI Agent Coordinator",
            "functions": ["Workforce Management", "Scheduling & Dispatching",
                          "Document Management"],
            "blurb": "Skill shows up in the data — efficiency, overtime and scrap per operator.",
            "kpis": [
                _kpi("Operator efficiency", round(op_eff, 1), "%", _status(op_eff, 90, 80),
                     "ideal time / actual run time"),
                _kpi("Labour productivity", round(uplh, 1), "units/labour-h", "good", "plant average"),
                _kpi("Overtime ratio", round(after_hours, 1), "%",
                     _status(after_hours, 5, 12, False), "orders finishing off-shift"),
                _kpi("Training/cert compliance", round(certified, 1), "%",
                     _status(certified, 90, 75), "orders run by skill ≥ 3 operators"),
                _na("Absenteeism / attendance", "time & attendance system feed"),
            ],
            "table": _rows(ops.sort_values("scrap_pct", ascending=False)
                           [["operator", "operator_shift", "efficiency_pct",
                             "units_per_hour", "scrap_pct"]],
                           ["operator", "operator_shift", "efficiency_pct",
                            "units_per_hour", "scrap_pct"]),
        })

    # ======================================================================
    # 5. MAINTENANCE — maintenance mgmt, downtime tracking, resource mgmt,
    #    inventory interface (spares)
    # ======================================================================
    pm = dt[dt["category"] == m.CAT_PLANNED_MAINTENANCE] if not dt.empty else dt
    machine_months = (wo.groupby(["machine", "month"]).size().reset_index()
                        .groupby("machine").size().sum())
    pm_months = (pm.groupby(["machine", "month"]).size().reset_index()
                   .groupby("machine").size().sum()) if not pm.empty else 0
    pm_compliance = pm_months / max(machine_months, 1) * 100
    rate_by_machine = wo.groupby("machine")["hourly_rate"].first()
    brk_cost = ((brk.groupby("machine")["duration_min"].sum() / 60 * rate_by_machine)
                .sum() if not brk.empty else 0.0)
    pm_cost = ((pm.groupby("machine")["duration_min"].sum() / 60 * rate_by_machine)
               .sum() if not pm.empty else 0.0)
    maint_cost_unit = (brk_cost + pm_cost) / max(good_units, 1)
    per_m = pd.DataFrame({"run_hours": wo.groupby("machine")["run_time_min"].sum() / 60})
    per_m["breakdowns"] = brk.groupby("machine")["duration_min"].count() if not brk.empty else 0
    per_m["mttr_min"] = brk.groupby("machine")["duration_min"].mean() if not brk.empty else 0.0
    per_m = per_m.fillna(0.0)
    per_m["mtbf_h"] = (per_m["run_hours"] / per_m["breakdowns"].replace(0, pd.NA))
    per_m = per_m.reset_index().fillna({"mtbf_h": per_m["run_hours"].max()})
    subsystems.append({
        "key": "maintenance", "name": "Maintenance", "icon": "🔧",
        "agent": "maintenance-strategist", "deck_agent": "Diagnosis Agent",
        "functions": ["Maintenance Management", "Downtime Tracking & Analysis",
                      "Resource Management", "Inventory & Material Management (spares)"],
        "blurb": "Machines whisper before they scream — MTBF, MTTR and what upkeep really costs per part.",
        "kpis": [
            _kpi("Maintenance cost/unit", round(maint_cost_unit, 3), "$/unit",
                 _status(maint_cost_unit, 0.05, 0.12, False),
                 f"breakdown ${brk_cost:,.0f} + PM ${pm_cost:,.0f}"),
            _kpi("PM compliance", round(pm_compliance, 1), "%", _status(pm_compliance, 90, 60),
                 "machine-months with a PM done"),
            _kpi("MTBF", round(mtbf, 1), "run-h", _status(mtbf, 100, 50), "plant average"),
            _kpi("MTTR", round(float(mttr), 1), "min", _status(float(mttr), 60, 100, False),
                 "plant average"),
            _na("Predictive maintenance accuracy", "prediction log vs actual failures"),
            _na("Spare parts availability", "spares stockroom system (CMMS)"),
        ],
        "table": _rows(per_m.sort_values("mtbf_h")[["machine", "breakdowns", "mtbf_h", "mttr_min"]],
                       ["machine", "breakdowns", "mtbf_h", "mttr_min"]),
    })

    # ======================================================================
    # 6. ENERGY, SUSTAINABILITY & COSTING — energy & utility monitoring,
    #    environmental monitoring, costing & variance, performance analysis
    # ======================================================================
    kwh = wo["kwh"].sum()
    e_cost = wo["energy_cost"].sum()
    kwh_unit = kwh / max(good_units, 1)
    co2_unit = kwh_unit * GRID_CO2_KG_PER_KWH
    waste_pct = scrap / max(total, 1) * 100
    time_cost = (wo["window_min"] / 60 * wo["hourly_rate"]).sum()
    mat_cost = (wo["total_count"] * wo["unit_material_cost"]).sum()
    cost_unit = (time_cost + mat_cost + e_cost) / max(good_units, 1)
    std_unit = ((wo["ideal_cycle_time_sec"] / 3600 * wo["hourly_rate"]
                 + wo["unit_material_cost"]) * wo["total_count"]).sum() / max(total, 1)
    variance = (cost_unit - std_unit) / max(std_unit, 0.01) * 100
    e_m = (wo.groupby("machine", as_index=False)
             .agg(kwh=("kwh", "sum"), good=("good_count", "sum"), cost=("energy_cost", "sum")))
    e_m["kwh_per_unit"] = (e_m["kwh"] / e_m["good"].clip(lower=1)).round(3)
    subsystems.append({
        "key": "energy", "name": "Energy, Sustainability & Costing", "icon": "⚡",
        "agent": "energy-optimizer", "deck_agent": "Energy Agent",
        "functions": ["Energy & Utility Monitoring", "Environmental & Sustainability Monitoring",
                      "Costing & Variance Analysis", "Performance Analysis"],
        "blurb": "Watts, waste and what a unit truly costs vs what it should.",
        "kpis": [
            _kpi("Energy per unit", round(kwh_unit, 3), "kWh/unit",
                 _status(kwh_unit, 0.5, 1.0, False), f"{kwh:,.0f} kWh · ${e_cost:,.0f}"),
            _kpi("Emissions per unit", round(co2_unit * 1000), "gCO₂e/unit",
                 _status(co2_unit, 0.2, 0.4, False), f"grid factor {GRID_CO2_KG_PER_KWH} kg/kWh"),
            _kpi("Waste per unit", round(waste_pct, 2), "%", _status(waste_pct, 1.5, 4, False),
                 "scrapped share of production"),
            _kpi("Production cost/unit", round(cost_unit, 2), "$/unit",
                 _status(variance, 15, 30, False), "machine time + material + energy"),
            _kpi("Variance vs standard", round(variance, 1), "%",
                 _status(variance, 15, 30, False), f"standard ${std_unit:.2f}/unit"),
            _na("Water per unit", "water metering (utility feed)"),
            _na("Return on assets", "asset register / balance sheet"),
        ],
        "table": _rows(e_m.sort_values("kwh_per_unit", ascending=False),
                       ["machine", "kwh", "cost", "kwh_per_unit"]),
    })

    # ======================================================================
    # 7. ORDER FULFILMENT & LOGISTICS — scheduling & dispatching, WO mgmt,
    #    warehouse & logistics interface, document mgmt, compliance & reporting
    # ======================================================================
    otd = adherence
    fulfil_h = lead_h
    otif = ((wo["end_delay_min"] <= ON_TIME_GRACE_MIN)
            & (wo["good_count"] >= wo["planned_qty"] * 0.98)).mean() * 100
    sla = (wo["end_delay_min"] <= ON_TIME_GRACE_MIN * 4).mean() * 100  # 2h SLA window
    ful_m = (wo.assign(otif=(wo["end_delay_min"] <= ON_TIME_GRACE_MIN)
                            & (wo["good_count"] >= wo["planned_qty"] * 0.98))
               .groupby("machine", as_index=False)
               .agg(orders=("wo_id", "count"), otif_pct=("otif", "mean")))
    ful_m["otif_pct"] = (ful_m["otif_pct"] * 100).round(1)
    subsystems.append({
        "key": "fulfilment", "name": "Order Fulfilment & Logistics", "icon": "🚚",
        "agent": "schedule-auditor", "deck_agent": "Planner Agent",
        "functions": ["Scheduling & Dispatching", "Work Order Management",
                      "Warehouse & Logistics Interface", "Document Management",
                      "Compliance & Reporting"],
        "blurb": "The promise-keeping subsystem: did the right amount leave on the right day?",
        "kpis": [
            _kpi("On-Time Delivery", round(otd, 1), "%", _status(otd, 90, 70),
                 f"{ON_TIME_GRACE_MIN:.0f} min grace"),
            _kpi("Fulfilment cycle time", round(fulfil_h, 1), "h",
                 _status(fulfil_h, 12, 24, False), "schedule → completion"),
            _kpi("Order accuracy (OTIF)", round(otif, 1), "%", _status(otif, 85, 65),
                 "on time AND ≥98% of quantity"),
            _kpi("SLA compliance", round(sla, 1), "%", _status(sla, 95, 85),
                 "within 2 h of promise"),
        ],
        "table": _rows(ful_m.sort_values("otif_pct"), ["machine", "orders", "otif_pct"]),
    })

    return {"subsystems": subsystems, "days": int(days)}
