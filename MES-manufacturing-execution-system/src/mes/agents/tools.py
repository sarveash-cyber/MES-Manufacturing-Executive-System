"""Custom tools the agents call. Agents never hand-compute KPIs — every number
comes from the deterministic KPI engine via these tools."""

from __future__ import annotations

from typing import Any

import pandas as pd
from claude_agent_sdk import create_sdk_mcp_server, tool
from sqlalchemy import text

from mes.db.session import get_session
from mes.kpi.frames import downtime_frame, work_order_frame
from mes.kpi.losses import downtime_pareto, schedule_adherence, six_big_losses
from mes.kpi.money import money_leak_totals, money_leaks
from mes.kpi.oee import oee_summary

VALID_GROUPS = {"machine", "product", "month", "work_center"}


def _text(payload: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": payload}]}


def _df_text(df: pd.DataFrame, limit: int = 60) -> str:
    if df.empty:
        return "(no data — has the database been seeded? run scripts/seed.py)"
    note = "" if len(df) <= limit else f"\n... ({len(df) - limit} more rows truncated)"
    return df.head(limit).to_string(index=False) + note


def _groups(args: dict[str, Any]) -> list[str]:
    raw = args.get("group_by") or "machine"
    groups = [g.strip() for g in raw.split(",") if g.strip() in VALID_GROUPS]
    return groups or ["machine"]


def _frame(args: dict[str, Any]) -> pd.DataFrame:
    from datetime import datetime

    def parse(key: str):
        v = args.get(key)
        return datetime.fromisoformat(v) if v else None

    session = get_session()
    try:
        return work_order_frame(session, parse("start"), parse("end"))
    finally:
        session.close()


DATE_PARAMS = {"start": str, "end": str}


@tool("get_data_overview",
      "Overview of the production database: machines, products, date range, "
      "order counts. Call this first to understand what data exists.", {})
async def get_data_overview(args: dict[str, Any]) -> dict[str, Any]:
    df = _frame({})
    if df.empty:
        return _text("Database is empty — run scripts/seed.py first.")
    lines = [
        f"Work orders: {len(df)}",
        f"Date range: {df['actual_start'].min():%Y-%m-%d} to {df['actual_end'].max():%Y-%m-%d}",
        f"Machines: {', '.join(sorted(df['machine'].unique()))}",
        f"Products: {', '.join(sorted(df['product'].unique()))}",
        f"Total units: {int(df['total_count'].sum()):,} "
        f"(scrap {int(df['scrap_count'].sum()):,})",
    ]
    return _text("\n".join(lines))


@tool("get_oee",
      "OEE and components (availability, performance, quality, utilization, TEEP) "
      "aggregated by group_by (comma list of: machine, product, month, work_center). "
      "Optional ISO date filters start/end.",
      {"group_by": str, **DATE_PARAMS})
async def get_oee(args: dict[str, Any]) -> dict[str, Any]:
    return _text(_df_text(oee_summary(_frame(args), _groups(args))))


@tool("get_six_big_losses",
      "Six Big Losses minutes (breakdown, changeover, minor stops, speed loss, "
      "quality loss) by group_by (machine, product, month, work_center).",
      {"group_by": str, **DATE_PARAMS})
async def get_six_big_losses(args: dict[str, Any]) -> dict[str, Any]:
    return _text(_df_text(six_big_losses(_frame(args), _groups(args))))


@tool("get_downtime_pareto",
      "Downtime Pareto (minutes, event count, cumulative %) by reason_code, "
      "category, or machine. Unplanned downtime only.",
      {"by": str, **DATE_PARAMS})
async def get_downtime_pareto(args: dict[str, Any]) -> dict[str, Any]:
    from datetime import datetime

    by = args.get("by") or "reason_code"
    if by not in ("reason_code", "category", "machine"):
        by = "reason_code"
    session = get_session()
    try:
        start = datetime.fromisoformat(args["start"]) if args.get("start") else None
        end = datetime.fromisoformat(args["end"]) if args.get("end") else None
        dt = downtime_frame(session, start, end)
    finally:
        session.close()
    return _text(_df_text(downtime_pareto(dt, by=by)))


@tool("get_schedule_adherence",
      "Schedule adherence: on-time %, average start delay and overrun minutes "
      "vs schedule, by group_by (machine, product, month).",
      {"group_by": str, **DATE_PARAMS})
async def get_schedule_adherence(args: dict[str, Any]) -> dict[str, Any]:
    return _text(_df_text(schedule_adherence(_frame(args), _groups(args))))


@tool("get_money_leaks",
      "Money lost ($) per loss bucket (breakdown, changeover, minor stops, speed "
      "loss, scrap) by group_by. Set totals=true for a plant-wide ranked summary. "
      "This is the primary tool for 'where am I bleeding money'.",
      {"group_by": str, "totals": bool, **DATE_PARAMS})
async def get_money_leaks(args: dict[str, Any]) -> dict[str, Any]:
    df = _frame(args)
    if args.get("totals"):
        return _text(_df_text(money_leak_totals(df)))
    return _text(_df_text(money_leaks(df, _groups(args))))


@tool("get_subsystem_kpis",
      "Full KPI matrix for one MES subsystem domain or all of them. Domains: "
      "production (OEE, throughput, capacity utilization, cycle/takt/lead time, "
      "adherence, changeover, downtime, MTBF/MTTR), quality (FPY, RFT, scrap, "
      "rework, DPMO, CoQ, audit & traceability), inventory (WIP, turns, raw "
      "material availability, material yield, batch utilization), workforce "
      "(operator efficiency, labour productivity, overtime, certification), "
      "maintenance (cost/unit, PM compliance, MTBF/MTTR), energy (kWh/unit, "
      "emissions, cost/unit, variance vs standard), fulfilment (OTD, cycle "
      "time, OTIF, SLA). Pass subsystem='' for the whole matrix.",
      {"subsystem": str})
async def get_subsystem_kpis(args: dict[str, Any]) -> dict[str, Any]:
    from mes.kpi.subsystems import subsystem_matrix

    session = get_session()
    try:
        mat = subsystem_matrix(session)
    finally:
        session.close()
    want = (args.get("subsystem") or "").strip().lower()
    lines: list[str] = []
    for sub in mat.get("subsystems", []):
        if want and sub["key"] != want:
            continue
        lines.append(f"## {sub['name']} (agent: {sub['agent']})")
        for k in sub["kpis"]:
            lines.append(f"- {k['name']}: {k['value']} {k['unit']} "
                         f"[{k['status']}] {k['note']}")
        rows = sub.get("table", {}).get("rows", [])
        if rows:
            lines.append(pd.DataFrame(rows).to_string(index=False))
        lines.append("")
    return _text("\n".join(lines) or f"unknown subsystem {want!r}")


@tool("run_readonly_sql",
      "Run a read-only SQL SELECT against the MES SQLite database. Tables: "
      "machines, products, cost_rates, operators, work_orders (has batch_number, "
      "operator_id), production_runs, quality_records, downtime_events, "
      "energy_readings, inventory_snapshots. Use for drill-downs the KPI tools "
      "don't cover.",
      {"sql": str})
async def run_readonly_sql(args: dict[str, Any]) -> dict[str, Any]:
    sql = (args.get("sql") or "").strip().rstrip(";")
    if not sql.lower().startswith("select") or ";" in sql:
        return _text("Rejected: only single SELECT statements are allowed.")
    session = get_session()
    try:
        df = pd.read_sql(text(sql), session.get_bind())
    except Exception as e:
        return _text(f"SQL error: {e}")
    finally:
        session.close()
    return _text(_df_text(df, limit=100))


ALL_TOOLS = [
    get_data_overview, get_oee, get_six_big_losses, get_downtime_pareto,
    get_schedule_adherence, get_money_leaks, get_subsystem_kpis,
    run_readonly_sql,
]

TOOL_NAMES = [f"mcp__kpi__{t.name}" for t in ALL_TOOLS]


def kpi_server():
    return create_sdk_mcp_server(name="kpi", version="1.0.0", tools=ALL_TOOLS)
