"""Shared pandas frames the KPI modules aggregate over.

`work_order_frame` returns one row per completed work order with all time
buckets pre-computed; `downtime_frame` returns one row per downtime event.
All durations are minutes.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from mes.db import models as m

UNPLANNED_CATS = (m.CAT_BREAKDOWN, m.CAT_CHANGEOVER, m.CAT_MINOR_STOP)


def work_order_frame(session: Session, start: datetime | None = None,
                     end: datetime | None = None) -> pd.DataFrame:
    stmt = (
        select(
            m.WorkOrder.id.label("wo_id"),
            m.WorkOrder.code.label("work_order"),
            m.Machine.code.label("machine"),
            m.Machine.work_center,
            m.Product.code.label("product"),
            m.Product.ideal_cycle_time_sec,
            m.Product.unit_material_cost,
            m.Product.unit_price,
            m.WorkOrder.scheduled_start, m.WorkOrder.scheduled_end,
            m.WorkOrder.actual_start, m.WorkOrder.actual_end,
            m.WorkOrder.planned_qty,
            m.ProductionRun.total_count,
            m.QualityRecord.good_count, m.QualityRecord.scrap_count,
            m.QualityRecord.rework_count,
            m.CostRate.hourly_rate,
            m.WorkOrder.batch_number,
            m.Operator.code.label("operator"),
            m.Operator.skill_level.label("operator_skill"),
            m.Operator.shift.label("operator_shift"),
            m.EnergyReading.kwh,
            m.EnergyReading.cost.label("energy_cost"),
        )
        .join(m.Machine, m.WorkOrder.machine_id == m.Machine.id)
        .join(m.Product, m.WorkOrder.product_id == m.Product.id)
        .join(m.ProductionRun, m.ProductionRun.work_order_id == m.WorkOrder.id)
        .join(m.QualityRecord, m.QualityRecord.run_id == m.ProductionRun.id)
        .join(m.CostRate, m.CostRate.machine_id == m.Machine.id, isouter=True)
        .join(m.Operator, m.WorkOrder.operator_id == m.Operator.id, isouter=True)
        .join(m.EnergyReading, m.EnergyReading.work_order_id == m.WorkOrder.id,
              isouter=True)
        .where(m.WorkOrder.status == "completed",
               m.WorkOrder.actual_start.is_not(None),
               m.WorkOrder.actual_end.is_not(None))
    )
    if start is not None:
        stmt = stmt.where(m.WorkOrder.actual_start >= start)
    if end is not None:
        stmt = stmt.where(m.WorkOrder.actual_start < end)

    df = pd.read_sql(stmt, session.get_bind())
    if df.empty:
        return df

    for col in ("scheduled_start", "scheduled_end", "actual_start", "actual_end"):
        df[col] = pd.to_datetime(df[col])
    df["hourly_rate"] = df["hourly_rate"].fillna(0.0)
    df["kwh"] = df["kwh"].fillna(0.0)
    df["energy_cost"] = df["energy_cost"].fillna(0.0)
    df["month"] = df["actual_start"].dt.to_period("M").astype(str)

    dt = downtime_frame(session, start, end)
    if dt.empty:
        agg = pd.DataFrame(columns=["wo_id"])
    else:
        agg = (dt.pivot_table(index="wo_id", columns="category",
                              values="duration_min", aggfunc="sum")
                 .reset_index())
    df = df.merge(agg, on="wo_id", how="left")
    for cat in m.DOWNTIME_CATEGORIES:
        if cat not in df.columns:
            df[cat] = 0.0
        df[cat] = df[cat].fillna(0.0)

    # time accounting (minutes)
    df["window_min"] = (df["actual_end"] - df["actual_start"]).dt.total_seconds() / 60
    df["planned_downtime_min"] = df[m.CAT_PLANNED_MAINTENANCE]
    df["unplanned_downtime_min"] = df[list(UNPLANNED_CATS)].sum(axis=1)
    # planned production time excludes planned maintenance
    df["planned_production_min"] = (df["window_min"] - df["planned_downtime_min"]).clip(lower=0)
    df["run_time_min"] = (df["planned_production_min"] - df["unplanned_downtime_min"]).clip(lower=0)
    df["ideal_time_min"] = df["ideal_cycle_time_sec"] * df["total_count"] / 60
    df["speed_loss_min"] = (df["run_time_min"] - df["ideal_time_min"]).clip(lower=0)
    df["quality_loss_min"] = df["ideal_cycle_time_sec"] * df["scrap_count"] / 60

    # schedule adherence inputs
    df["start_delay_min"] = ((df["actual_start"] - df["scheduled_start"])
                             .dt.total_seconds() / 60)
    df["end_delay_min"] = ((df["actual_end"] - df["scheduled_end"])
                           .dt.total_seconds() / 60)
    return df


def downtime_frame(session: Session, start: datetime | None = None,
                   end: datetime | None = None) -> pd.DataFrame:
    stmt = (
        select(
            m.DowntimeEvent.work_order_id.label("wo_id"),
            m.Machine.code.label("machine"),
            m.DowntimeEvent.reason_code,
            m.DowntimeEvent.description,
            m.DowntimeEvent.category,
            m.DowntimeEvent.is_planned,
            m.DowntimeEvent.start,
            m.DowntimeEvent.duration_min,
            m.CostRate.hourly_rate,
        )
        .join(m.Machine, m.DowntimeEvent.machine_id == m.Machine.id)
        .join(m.CostRate, m.CostRate.machine_id == m.Machine.id, isouter=True)
    )
    if start is not None:
        stmt = stmt.where(m.DowntimeEvent.start >= start)
    if end is not None:
        stmt = stmt.where(m.DowntimeEvent.start < end)
    df = pd.read_sql(stmt, session.get_bind())
    if df.empty:
        return df
    df["start"] = pd.to_datetime(df["start"])
    df["hourly_rate"] = df["hourly_rate"].fillna(0.0)
    df["month"] = df["start"].dt.to_period("M").astype(str)
    return df
