"""Loss analysis: Six Big Losses, downtime Pareto, schedule adherence."""

from __future__ import annotations

import pandas as pd

from mes.db import models as m

ON_TIME_GRACE_MIN = 30.0

LOSS_LABELS = {
    m.CAT_BREAKDOWN: "Breakdowns (availability)",
    m.CAT_CHANGEOVER: "Setup & changeover (availability)",
    m.CAT_MINOR_STOP: "Minor stops (availability)",
    "speed_loss": "Reduced speed (performance)",
    "quality_loss": "Scrap & rejects (quality)",
}


def six_big_losses(wo_df: pd.DataFrame,
                   group_by: list[str] | None = None) -> pd.DataFrame:
    """Minutes lost per Six-Big-Losses bucket, long format, largest first."""
    if wo_df.empty:
        return pd.DataFrame()
    group_by = group_by or ["machine"]
    agg = wo_df.groupby(group_by, as_index=False)[
        [m.CAT_BREAKDOWN, m.CAT_CHANGEOVER, m.CAT_MINOR_STOP,
         "speed_loss_min", "quality_loss_min"]].sum()
    agg = agg.rename(columns={"speed_loss_min": "speed_loss",
                              "quality_loss_min": "quality_loss"})
    long = agg.melt(id_vars=group_by, var_name="loss_bucket",
                    value_name="minutes_lost")
    long["label"] = long["loss_bucket"].map(LOSS_LABELS)
    long["minutes_lost"] = long["minutes_lost"].round(1)
    return (long.sort_values("minutes_lost", ascending=False)
                .reset_index(drop=True))


def downtime_pareto(dt_df: pd.DataFrame, by: str = "reason_code",
                    include_planned: bool = False) -> pd.DataFrame:
    """Downtime Pareto by reason_code | category | machine, with cumulative %."""
    if dt_df.empty:
        return pd.DataFrame()
    df = dt_df if include_planned else dt_df[~dt_df["is_planned"]]
    g = (df.groupby([by, "description"] if by == "reason_code" else [by],
                    as_index=False)
           .agg(minutes=("duration_min", "sum"), events=("duration_min", "count")))
    g = g.sort_values("minutes", ascending=False).reset_index(drop=True)
    total = g["minutes"].sum()
    g["pct"] = (g["minutes"] / total * 100).round(1) if total else 0.0
    g["cum_pct"] = g["pct"].cumsum().round(1)
    g["minutes"] = g["minutes"].round(1)
    return g


def schedule_adherence(wo_df: pd.DataFrame,
                       group_by: list[str] | None = None) -> pd.DataFrame:
    """On-time completion %, average start delay and overrun vs schedule."""
    if wo_df.empty:
        return pd.DataFrame()
    group_by = group_by or ["machine"]
    df = wo_df.copy()
    df["on_time"] = df["end_delay_min"] <= ON_TIME_GRACE_MIN
    g = df.groupby(group_by, as_index=False).agg(
        orders=("wo_id", "count"),
        on_time_pct=("on_time", "mean"),
        avg_start_delay_min=("start_delay_min", "mean"),
        avg_overrun_min=("end_delay_min", "mean"),
    )
    g["on_time_pct"] = (g["on_time_pct"] * 100).round(1)
    g["avg_start_delay_min"] = g["avg_start_delay_min"].round(1)
    g["avg_overrun_min"] = g["avg_overrun_min"].round(1)
    return g.sort_values("on_time_pct").reset_index(drop=True)
