"""Synthetic MES history generator.

Produces 12 months of realistic production history for 6 machines with
deliberate, discoverable problems so the KPI engine and agents have real
stories to find:

- CNC-01: aging spindle — breakdown frequency grows month over month
- CNC-02: chronic minor stops (chip jams, sensor trips)
- PRESS-01: long die changeovers
- ASM-01: high scrap, worst on valve bodies (fixture misalignment)
- PACK-01: chronic speed loss (running ~80% of rated speed)
- GRD-01: the healthy benchmark machine

All quantities are generated so the identity holds per work order:
actual window = run time + logged downtime, run time = ideal time / speed factor.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from mes.db import models as m

SHIFT_START_HOUR = 6
SHIFT_END_HOUR = 22  # two shifts, 06:00-22:00

PRODUCTS = [
    # code, name, ideal cycle sec/unit, material cost, unit price
    ("VLV-20", "Valve body 20mm", 45.0, 3.20, 9.50),
    ("PMP-10", "Pump housing 10L", 90.0, 7.50, 21.00),
    ("BRK-05", "Mounting bracket", 20.0, 0.80, 2.60),
    ("GRS-30", "Gear set 30T", 120.0, 11.00, 30.00),
    ("FLT-15", "Filter cap", 15.0, 0.50, 1.90),
]

MACHINES = [
    # code, name, work center, hourly cost rate, rated kW, products it runs
    ("CNC-01", "CNC mill 1 (aging spindle)", "machining", 145.0, 32.0, ["VLV-20", "PMP-10"]),
    ("CNC-02", "CNC mill 2", "machining", 145.0, 30.0, ["VLV-20", "GRS-30"]),
    ("PRESS-01", "Stamping press 1", "forming", 120.0, 58.0, ["BRK-05", "FLT-15"]),
    ("ASM-01", "Assembly cell 1", "assembly", 95.0, 9.0, ["VLV-20", "PMP-10", "GRS-30"]),
    ("PACK-01", "Packaging line 1", "packaging", 80.0, 14.0, ["FLT-15", "BRK-05"]),
    ("GRD-01", "Grinder 1", "machining", 110.0, 26.0, ["GRS-30", "PMP-10"]),
]

TARIFF = 0.18  # $/kWh
IDLE_DRAW = 0.25  # fraction of rated kW drawn while down within the window

OPERATORS = [
    # code, name, shift, skill 1-5, hourly rate
    ("OP-01", "A. Marsh", "DAY", 5, 38.0),
    ("OP-02", "B. Okafor", "DAY", 4, 34.0),
    ("OP-03", "C. Reyes", "DAY", 3, 31.0),
    ("OP-04", "D. Lindqvist", "SWING", 4, 34.0),
    ("OP-05", "E. Tanaka", "SWING", 3, 31.0),
    ("OP-06", "F. Novak", "SWING", 2, 28.0),
    ("OP-07", "G. Pillai (trainee)", "DAY", 1, 24.0),
    ("OP-08", "H. Duarte", "SWING", 5, 38.0),
]

DOWNTIME_REASONS = {
    "BRG-FAIL": ("Spindle bearing failure", m.CAT_BREAKDOWN),
    "HYD-LEAK": ("Hydraulic leak", m.CAT_BREAKDOWN),
    "E-STOP": ("Emergency stop / fault reset", m.CAT_BREAKDOWN),
    "DIE-SWAP": ("Die / tooling changeover", m.CAT_CHANGEOVER),
    "SETUP": ("Product setup & first-article check", m.CAT_CHANGEOVER),
    "CHIP-JAM": ("Chip conveyor jam", m.CAT_MINOR_STOP),
    "SENSOR": ("Sensor trip / misread", m.CAT_MINOR_STOP),
    "MAT-WAIT": ("Waiting on material", m.CAT_MINOR_STOP),
    "PM": ("Planned maintenance", m.CAT_PLANNED_MAINTENANCE),
}


def _next_shift_slot(t: datetime) -> datetime:
    """Clamp a timestamp into the 06:00-22:00 working window."""
    if t.hour >= SHIFT_END_HOUR:
        t = (t + timedelta(days=1)).replace(hour=SHIFT_START_HOUR, minute=0, second=0)
    elif t.hour < SHIFT_START_HOUR:
        t = t.replace(hour=SHIFT_START_HOUR, minute=0, second=0)
    return t


def _month_index(start: datetime, t: datetime) -> int:
    return (t.year - start.year) * 12 + (t.month - start.month)


def generate(session: Session, months: int = 12, seed: int = 42,
             end: datetime | None = None) -> dict:
    """Populate the session's database with synthetic history. Returns counts."""
    rng = random.Random(seed)
    end = end or datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = (end - timedelta(days=30 * months)).replace(day=1, hour=SHIFT_START_HOUR,
                                                        minute=0, second=0, microsecond=0)

    products = {code: m.Product(code=code, name=name, ideal_cycle_time_sec=ct,
                                unit_material_cost=mat, unit_price=price)
                for code, name, ct, mat, price in PRODUCTS}
    session.add_all(products.values())

    machines: dict[str, m.Machine] = {}
    machine_kw: dict[str, float] = {}
    for code, name, wc, rate, kw, _ in MACHINES:
        mach = m.Machine(code=code, name=name, work_center=wc)
        machines[code] = mach
        machine_kw[code] = kw
        session.add(mach)
        session.add(m.CostRate(machine=mach, hourly_rate=rate))

    operators: dict[str, list[m.Operator]] = {"DAY": [], "SWING": []}
    for code, name, shift, skill, wage in OPERATORS:
        op = m.Operator(code=code, name=name, shift=shift,
                        skill_level=skill, hourly_rate=wage)
        operators[shift].append(op)
        session.add(op)
    session.flush()

    counts = {"work_orders": 0, "downtime_events": 0, "energy_readings": 0}
    order_seq = 1

    for mcode, _, _, _, _, prod_codes in MACHINES:
        mach = machines[mcode]
        cursor = start
        last_product: str | None = None
        pm_done_for_month = -1

        while cursor < end:
            cursor = _next_shift_slot(cursor)
            month = _month_index(start, cursor)
            pcode = rng.choice(prod_codes)
            prod = products[pcode]

            # size the order to roughly 3-7 hours of ideal run time
            target_hours = rng.uniform(3.0, 7.0)
            qty = max(20, int(target_hours * 3600 / prod.ideal_cycle_time_sec))
            ideal_min = qty * prod.ideal_cycle_time_sec / 60.0

            scheduled_start = cursor
            scheduled_end = scheduled_start + timedelta(minutes=ideal_min * 1.15)

            # --- actuals ---
            actual_start = scheduled_start + timedelta(minutes=rng.uniform(0, 35))
            events: list[tuple[str, float]] = []  # (reason_code, minutes)

            # changeover when the product switches (PRESS-01 is notoriously slow)
            if pcode != last_product:
                if mcode == "PRESS-01":
                    events.append(("DIE-SWAP", rng.uniform(45, 95)))
                else:
                    events.append(("SETUP", rng.uniform(8, 25)))

            # breakdowns — CNC-01 degrades over the year
            p_breakdown = 0.06
            if mcode == "CNC-01":
                p_breakdown = 0.08 + 0.025 * month  # ~0.08 -> ~0.35
            if rng.random() < p_breakdown:
                reason = "BRG-FAIL" if mcode == "CNC-01" and rng.random() < 0.7 else \
                    rng.choice(["HYD-LEAK", "E-STOP"])
                events.append((reason, rng.uniform(35, 180)))

            # minor stops — CNC-02 is the repeat offender
            n_stops = rng.randint(2, 6) if mcode == "CNC-02" else rng.randint(0, 2)
            for _ in range(n_stops):
                events.append((rng.choice(["CHIP-JAM", "SENSOR", "MAT-WAIT"]),
                               rng.uniform(2, 9)))

            # one planned maintenance block per machine per month
            if month != pm_done_for_month and rng.random() < 0.35:
                events.append(("PM", rng.uniform(120, 240)))
                pm_done_for_month = month

            # speed factor (actual vs rated speed) — PACK-01 bleeds here
            if mcode == "PACK-01":
                speed = rng.uniform(0.76, 0.85)
            elif mcode == "GRD-01":
                speed = rng.uniform(0.95, 0.99)
            else:
                speed = rng.uniform(0.88, 0.97)

            run_min = ideal_min / speed
            downtime_min = sum(mins for _, mins in events)
            actual_end = actual_start + timedelta(minutes=run_min + downtime_min)

            # operator: pick from the shift that covers the start hour;
            # low-skill operators scrap noticeably more (workforce story)
            shift = "DAY" if actual_start.hour < 14 else "SWING"
            op = rng.choice(operators[shift])
            skill_scrap_mult = {1: 2.2, 2: 1.5, 3: 1.0, 4: 0.85, 5: 0.7}[op.skill_level]

            # scrap — ASM-01 is bad, and gets worse on VLV-20 late in the year
            base_scrap = 0.010
            if mcode == "ASM-01":
                base_scrap = 0.035
                if pcode == "VLV-20" and month >= 8:
                    base_scrap = 0.09
            base_scrap = min(0.25, base_scrap * skill_scrap_mult)
            scrap = sum(1 for _ in range(qty) if rng.random() < base_scrap)
            good = qty - scrap

            wo = m.WorkOrder(
                code=f"WO-{order_seq:05d}", machine=mach, product=prod,
                scheduled_start=scheduled_start, scheduled_end=scheduled_end,
                planned_qty=qty, actual_start=actual_start, actual_end=actual_end,
                status="completed",
                batch_number=f"{pcode}-{actual_start:%y%m}-{order_seq:05d}",
                operator=op,
            )
            order_seq += 1
            session.add(wo)

            run = m.ProductionRun(work_order=wo, start=actual_start, end=actual_end,
                                  total_count=qty)
            session.add(run)
            session.add(m.QualityRecord(
                run=run, good_count=good, scrap_count=scrap, rework_count=0,
                defect_reason="MISALIGN" if mcode == "ASM-01" and scrap else None))

            t = actual_start
            for reason, mins in events:
                desc, category = DOWNTIME_REASONS[reason]
                session.add(m.DowntimeEvent(
                    work_order=wo, machine_id=mach.id, reason_code=reason,
                    description=desc, category=category,
                    is_planned=(category == m.CAT_PLANNED_MAINTENANCE),
                    start=t, duration_min=round(mins, 1)))
                t += timedelta(minutes=mins)
                counts["downtime_events"] += 1

            # energy: running draw + idle draw during downtime.
            # PRESS-01 runs an inefficient old drive (energy story).
            eff = 1.35 if mcode == "PRESS-01" else rng.uniform(0.95, 1.08)
            kwh = (run_min / 60 * machine_kw[mcode] * eff
                   + downtime_min / 60 * machine_kw[mcode] * IDLE_DRAW)
            session.add(m.EnergyReading(work_order=wo, machine_id=mach.id,
                                        kwh=round(kwh, 1),
                                        cost=round(kwh * TARIFF, 2)))
            counts["energy_readings"] += 1

            counts["work_orders"] += 1
            last_product = pcode
            cursor = actual_end + timedelta(minutes=rng.uniform(10, 45))

    # month-end inventory snapshots per product; GRS-30 is chronically
    # overstocked (inventory story: expensive gears napping on the racks)
    snap_date = start.replace(day=1)
    daily_demand = {code: max(40, int(16 * 3600 / ct / 3))
                    for code, _, ct, _, _ in PRODUCTS}
    while snap_date < end:
        month_end = (snap_date + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        for code, _, _, mat, _ in PRODUCTS:
            days_cover = rng.uniform(45, 75) if code == "GRS-30" else rng.uniform(12, 28)
            session.add(m.InventorySnapshot(
                product_id=products[code].id, date=month_end,
                qty_on_hand=int(daily_demand[code] * days_cover),
                unit_cost=mat))
        snap_date = (snap_date + timedelta(days=32)).replace(day=1)

    session.commit()
    counts["machines"] = len(MACHINES)
    counts["products"] = len(PRODUCTS)
    counts["operators"] = len(OPERATORS)
    return counts
