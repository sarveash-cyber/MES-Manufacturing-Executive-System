"""Adapter for the UCI AI4I 2020 Predictive Maintenance dataset.

https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset

The dataset is 10,000 machining cycles (one row per unit) for three product
quality tiers (L/M/H) with failure-mode flags. It has no timestamps or
schedules, so this adapter synthesizes a time axis: rows are processed in UID
order, grouped into work orders of BATCH_SIZE units on one pseudo-machine per
tier. Failure rows become downtime events (duration by failure mode) and count
as scrap. Useful as downtime/quality enrichment, not as schedule history.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from mes.db import models as m

BATCH_SIZE = 200
CYCLE_SEC = {"L": 60.0, "M": 90.0, "H": 120.0}
MATERIAL = {"L": 2.0, "M": 4.0, "H": 8.0}
FAILURE_DURATION_MIN = {  # typical repair time per failure mode
    "TWF": 45.0,   # tool wear failure
    "HDF": 90.0,   # heat dissipation failure
    "PWF": 60.0,   # power failure
    "OSF": 75.0,   # overstrain failure
    "RNF": 30.0,   # random failure
}
FAILURE_DESC = {
    "TWF": "Tool wear failure", "HDF": "Heat dissipation failure",
    "PWF": "Power failure", "OSF": "Overstrain failure", "RNF": "Random failure",
}


def load(session: Session, csv_path: Path, start: datetime | None = None) -> dict:
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]
    start = start or datetime.now() - timedelta(days=180)

    products, machines = {}, {}
    for tier in ("L", "M", "H"):
        products[tier] = m.Product(
            code=f"AI4I-{tier}", name=f"AI4I part quality {tier}",
            ideal_cycle_time_sec=CYCLE_SEC[tier],
            unit_material_cost=MATERIAL[tier], unit_price=MATERIAL[tier] * 2.8)
        machines[tier] = m.Machine(code=f"MILL-{tier}", name=f"AI4I mill (tier {tier})",
                                   work_center="machining")
        session.add_all([products[tier], machines[tier]])
        session.add(m.CostRate(machine=machines[tier], hourly_rate=130.0))
    session.flush()

    counts = {"work_orders": 0, "downtime_events": 0}
    cursors = {t: start for t in ("L", "M", "H")}
    order_no = 1

    type_col = "Type"
    fail_col = next(c for c in df.columns if c.lower().startswith("machine failure"))

    for tier, group in df.groupby(type_col):
        for i in range(0, len(group), BATCH_SIZE):
            chunk = group.iloc[i:i + BATCH_SIZE]
            qty = len(chunk)
            ideal_min = qty * CYCLE_SEC[tier] / 60.0
            run_min = ideal_min / 0.93  # nominal speed factor

            failures = chunk[chunk[fail_col] == 1]
            events: list[tuple[str, float]] = []
            for _, frow in failures.iterrows():
                mode = next((k for k in FAILURE_DURATION_MIN if k in frow.index
                             and frow.get(k) == 1), "RNF")
                events.append((mode, FAILURE_DURATION_MIN[mode]))
            downtime_min = sum(mins for _, mins in events)

            t0 = cursors[tier]
            t1 = t0 + timedelta(minutes=run_min + downtime_min)
            wo = m.WorkOrder(
                code=f"AI4I-{order_no:05d}", machine=machines[tier],
                product=products[tier], scheduled_start=t0,
                scheduled_end=t0 + timedelta(minutes=ideal_min * 1.1),
                planned_qty=qty, actual_start=t0, actual_end=t1, status="completed")
            order_no += 1
            session.add(wo)
            run = m.ProductionRun(work_order=wo, start=t0, end=t1, total_count=qty)
            session.add(run)
            scrap = len(failures)
            session.add(m.QualityRecord(run=run, good_count=qty - scrap,
                                        scrap_count=scrap, rework_count=0,
                                        defect_reason="MACHINE-FAILURE" if scrap else None))
            t = t0
            for mode, mins in events:
                session.add(m.DowntimeEvent(
                    work_order=wo, machine_id=machines[tier].id, reason_code=mode,
                    description=FAILURE_DESC[mode], category=m.CAT_BREAKDOWN,
                    is_planned=False, start=t, duration_min=mins))
                t += timedelta(minutes=mins)
                counts["downtime_events"] += 1

            counts["work_orders"] += 1
            cursors[tier] = t1 + timedelta(minutes=20)

    session.commit()
    counts["machines"] = 3
    counts["products"] = 3
    return counts
