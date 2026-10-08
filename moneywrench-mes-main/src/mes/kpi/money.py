"""Money-bleed analysis: price every loss bucket in currency.

- lost machine time (breakdowns, changeovers, minor stops, speed loss):
  minutes / 60 x fully-burdened hourly machine rate
- scrap: units x (material cost + machine time consumed per unit)

This is the direct answer to "where am I bleeding money" — a ranked table of
$ lost by machine / loss bucket (optionally by product or month).
"""

from __future__ import annotations

import pandas as pd

from mes.db import models as m
from mes.kpi.losses import LOSS_LABELS

TIME_BUCKETS = [m.CAT_BREAKDOWN, m.CAT_CHANGEOVER, m.CAT_MINOR_STOP, "speed_loss_min"]


def money_leaks(wo_df: pd.DataFrame,
                group_by: list[str] | None = None) -> pd.DataFrame:
    """Long-format ranked $ losses per group and loss bucket."""
    if wo_df.empty:
        return pd.DataFrame()
    group_by = group_by or ["machine"]
    df = wo_df.copy()

    for bucket in TIME_BUCKETS:
        df[f"cost__{bucket}"] = df[bucket] / 60.0 * df["hourly_rate"]
    df["cost__quality_loss"] = df["scrap_count"] * (
        df["unit_material_cost"]
        + df["ideal_cycle_time_sec"] / 3600.0 * df["hourly_rate"])

    cost_cols = [f"cost__{b}" for b in TIME_BUCKETS] + ["cost__quality_loss"]
    min_cols = TIME_BUCKETS + ["quality_loss_min"]
    g = df.groupby(group_by, as_index=False)[cost_cols + min_cols].sum()

    rows = []
    for bucket, cost_col, min_col in zip(
            [m.CAT_BREAKDOWN, m.CAT_CHANGEOVER, m.CAT_MINOR_STOP,
             "speed_loss", "quality_loss"],
            cost_cols,
            min_cols):
        part = g[group_by].copy()
        part["loss_bucket"] = bucket
        part["label"] = LOSS_LABELS[bucket]
        part["minutes_lost"] = g[min_col].round(1)
        part["cost"] = g[cost_col].round(2)
        rows.append(part)
    out = pd.concat(rows, ignore_index=True)
    return (out.sort_values("cost", ascending=False)
               .reset_index(drop=True))


def money_leak_totals(wo_df: pd.DataFrame) -> pd.DataFrame:
    """One line per loss bucket across the whole plant, ranked."""
    leaks = money_leaks(wo_df, group_by=["machine"])
    if leaks.empty:
        return leaks
    g = (leaks.groupby(["loss_bucket", "label"], as_index=False)
              [["minutes_lost", "cost"]].sum()
              .sort_values("cost", ascending=False).reset_index(drop=True))
    total = g["cost"].sum()
    g["pct_of_total"] = (g["cost"] / total * 100).round(1) if total else 0.0
    return g
