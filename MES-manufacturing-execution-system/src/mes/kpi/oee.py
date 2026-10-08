"""OEE and components, aggregated the correct way: sum the time buckets per
group first, then take ratios (never average per-order percentages).

Definitions (industry standard, SEMI E10 / lean convention):
- Planned production time = machine-occupied window minus planned maintenance
- Availability = run time / planned production time
  (run time = planned production time minus all logged unplanned stops:
   breakdowns, changeovers, minor stops)
- Performance = ideal time / run time (pure speed loss; ideal time =
  ideal cycle time x total count)
- Quality = good count / total count
- OEE = A x P x Q
- Utilization = planned production time / calendar span; TEEP = OEE x Utilization
"""

from __future__ import annotations

import pandas as pd

SUM_COLS = [
    "window_min", "planned_downtime_min", "unplanned_downtime_min",
    "planned_production_min", "run_time_min", "ideal_time_min",
    "total_count", "good_count", "scrap_count",
]


def oee_summary(df: pd.DataFrame, group_by: list[str] | None = None) -> pd.DataFrame:
    """`df` is a work_order_frame. group_by: subset of machine/product/month/work_center."""
    if df.empty:
        return pd.DataFrame()
    group_by = group_by or ["machine"]

    g = df.groupby(group_by, as_index=False)[SUM_COLS].sum()

    # calendar span per group for utilization/TEEP
    span = (df.groupby(group_by)
              .apply(lambda x: (x["actual_end"].max() - x["actual_start"].min())
                     .total_seconds() / 60, include_groups=False)
              .rename("calendar_min").reset_index())
    g = g.merge(span, on=group_by)

    g["availability"] = (g["run_time_min"] / g["planned_production_min"]).clip(0, 1)
    g["performance"] = (g["ideal_time_min"] / g["run_time_min"]).clip(0, 1)
    g["quality"] = (g["good_count"] / g["total_count"]).clip(0, 1)
    g["oee"] = g["availability"] * g["performance"] * g["quality"]
    g["utilization"] = (g["planned_production_min"] / g["calendar_min"]).clip(0, 1)
    g["teep"] = g["oee"] * g["utilization"]

    for c in ("availability", "performance", "quality", "oee", "utilization", "teep"):
        g[c] = g[c].round(4)
    return g.sort_values(group_by).reset_index(drop=True)
