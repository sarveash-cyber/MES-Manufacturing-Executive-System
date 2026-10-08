"""Adapter for the Kaggle 'Manufacturing Downtime / Line Productivity' dataset.

https://www.kaggle.com/datasets/agungpambudi/predict-manufacturing-downtime-performance-dataset

Expected files (place in data/raw/kaggle_downtime/, exact names may vary
slightly between mirrors — matching is case-insensitive on normalized names):

- Line productivity.csv : Date, Product, Batch, Operator, Start Time, End Time
- Products.csv          : Product, Flavor, Size, Min batch time
- Line downtime.csv     : Batch x downtime-factor matrix (minutes)
- Downtime factors.csv  : Factor, Description, Operator Error

Mapping into the canonical model:
- one Machine per production line (dataset has a single line -> LINE-01)
- Products.csv 'Min batch time' -> ideal batch time; cycle time derived assuming
  a nominal batch quantity (units aren't in the dataset, so batch = 100 units)
- each batch -> WorkOrder + ProductionRun; scheduled duration = min batch time
- Line downtime minutes -> DowntimeEvent per factor, reason from factors file
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from mes.db import models as m

NOMINAL_BATCH_QTY = 100
DEFAULT_HOURLY_RATE = 100.0

# factors whose description mentions these words are treated as changeovers
_CHANGEOVER_HINTS = ("changeover", "change over", "setup", "adjustment", "calibration")


def _find_file(folder: Path, *keywords: str) -> Path | None:
    for f in folder.glob("*.csv"):
        name = re.sub(r"[^a-z]", "", f.stem.lower())
        if all(k in name for k in keywords):
            return f
    return None


def load(session: Session, folder: Path) -> dict:
    folder = Path(folder)
    prod_file = _find_file(folder, "lineproductivity") or _find_file(folder, "productivity")
    products_file = _find_file(folder, "products")
    downtime_file = _find_file(folder, "linedowntime") or _find_file(folder, "downtime")
    factors_file = _find_file(folder, "factors")
    if not (prod_file and products_file and downtime_file):
        raise FileNotFoundError(
            f"Expected the Kaggle downtime CSVs in {folder}. "
            "Download with: python scripts/seed.py --source kaggle-downtime --download")

    lines = pd.read_csv(prod_file)
    products = pd.read_csv(products_file)
    downtime = pd.read_csv(downtime_file)
    factors = pd.read_csv(factors_file) if factors_file else pd.DataFrame()

    lines.columns = [c.strip().lower() for c in lines.columns]
    products.columns = [c.strip().lower() for c in products.columns]
    factors.columns = [c.strip().lower() for c in factors.columns]

    machine = m.Machine(code="LINE-01", name="Bottling line 1", work_center="bottling")
    session.add(machine)
    session.add(m.CostRate(machine=machine, hourly_rate=DEFAULT_HOURLY_RATE))

    time_col = next(c for c in products.columns if "time" in c)
    prod_rows: dict[str, m.Product] = {}
    for _, row in products.iterrows():
        code = str(row["product"]).strip()
        min_batch_min = float(row[time_col])
        prod_rows[code] = m.Product(
            code=code, name=code,
            ideal_cycle_time_sec=min_batch_min * 60.0 / NOMINAL_BATCH_QTY,
            unit_material_cost=0.0, unit_price=0.0)
    session.add_all(prod_rows.values())
    session.flush()

    factor_desc: dict[str, tuple[str, str]] = {}
    if not factors.empty:
        fcol = factors.columns[0]
        dcol = next((c for c in factors.columns if "desc" in c), factors.columns[-1])
        for _, row in factors.iterrows():
            desc = str(row[dcol])
            cat = (m.CAT_CHANGEOVER
                   if any(h in desc.lower() for h in _CHANGEOVER_HINTS)
                   else m.CAT_BREAKDOWN)
            factor_desc[str(row[fcol]).strip()] = (desc, cat)

    downtime = downtime.set_index(downtime.columns[0])

    counts = {"work_orders": 0, "downtime_events": 0}
    for _, row in lines.iterrows():
        batch = str(row["batch"]).strip()
        date = pd.to_datetime(row["date"], dayfirst=False, errors="coerce")
        t_start = pd.to_datetime(f"{date.date()} {row['start time']}")
        t_end = pd.to_datetime(f"{date.date()} {row['end time']}")
        if t_end <= t_start:  # crossed midnight
            t_end += pd.Timedelta(days=1)

        pcode = str(row["product"]).strip()
        prod = prod_rows.get(pcode)
        if prod is None:
            continue
        ideal_min = prod.ideal_cycle_time_sec * NOMINAL_BATCH_QTY / 60.0

        wo = m.WorkOrder(
            code=f"BATCH-{batch}", machine=machine, product=prod,
            scheduled_start=t_start.to_pydatetime(),
            scheduled_end=(t_start + pd.Timedelta(minutes=ideal_min)).to_pydatetime(),
            planned_qty=NOMINAL_BATCH_QTY,
            actual_start=t_start.to_pydatetime(), actual_end=t_end.to_pydatetime(),
            status="completed")
        session.add(wo)
        run = m.ProductionRun(work_order=wo, start=wo.actual_start, end=wo.actual_end,
                              total_count=NOMINAL_BATCH_QTY)
        session.add(run)
        session.add(m.QualityRecord(run=run, good_count=NOMINAL_BATCH_QTY,
                                    scrap_count=0, rework_count=0))

        if batch in downtime.index.astype(str).tolist():
            drow = downtime.loc[downtime.index.astype(str) == batch].iloc[0]
            t = wo.actual_start
            for factor, mins in drow.items():
                mins = pd.to_numeric(mins, errors="coerce")
                if pd.isna(mins) or mins <= 0:
                    continue
                desc, cat = factor_desc.get(str(factor).strip(),
                                            (str(factor), m.CAT_BREAKDOWN))
                session.add(m.DowntimeEvent(
                    work_order=wo, machine_id=machine.id,
                    reason_code=str(factor).strip()[:32], description=desc[:256],
                    category=cat, is_planned=False, start=t,
                    duration_min=float(mins)))
                counts["downtime_events"] += 1
        counts["work_orders"] += 1

    session.commit()
    counts["machines"] = 1
    counts["products"] = len(prod_rows)
    return counts
