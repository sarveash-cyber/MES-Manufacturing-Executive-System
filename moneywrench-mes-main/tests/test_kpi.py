"""KPI formulas verified against a hand-computed single work order.

Fixture: machine M1 (rate $120/h), product P1 (60 s/unit ideal, $2 material).
Window 08:00-16:30 (510 min) = 420 run + 60 breakdown + 30 planned maintenance.
400 units produced, 380 good.

Hand-computed:
- planned production time = 510 - 30 = 480
- availability = 420/480 = 0.875
- performance = 400/420 = 0.952381
- quality = 380/400 = 0.95
- OEE = 0.79167
- breakdown cost = 1h x $120 = $120; speed loss = 20 min = $40
- scrap cost = 20 x ($2 material + 1 min x $2/min-> $2 machine) = $80
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine

from mes.db import models as m
from mes.db.session import get_session
from mes.kpi.frames import downtime_frame, work_order_frame
from mes.kpi.losses import downtime_pareto, schedule_adherence, six_big_losses
from mes.kpi.money import money_leak_totals, money_leaks
from mes.kpi.oee import oee_summary


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    m.Base.metadata.create_all(engine)
    s = get_session(engine)

    mach = m.Machine(code="M1", name="Test machine")
    prod = m.Product(code="P1", name="Test part", ideal_cycle_time_sec=60.0,
                     unit_material_cost=2.0, unit_price=6.0)
    s.add_all([mach, prod])
    s.add(m.CostRate(machine=mach, hourly_rate=120.0))
    s.flush()

    wo = m.WorkOrder(
        code="WO-1", machine=mach, product=prod,
        scheduled_start=datetime(2026, 1, 5, 8, 0),
        scheduled_end=datetime(2026, 1, 5, 15, 30),
        planned_qty=400,
        actual_start=datetime(2026, 1, 5, 8, 0),
        actual_end=datetime(2026, 1, 5, 16, 30),
        status="completed")
    s.add(wo)
    run = m.ProductionRun(work_order=wo, start=wo.actual_start, end=wo.actual_end,
                          total_count=400)
    s.add(run)
    s.add(m.QualityRecord(run=run, good_count=380, scrap_count=20))
    s.add(m.DowntimeEvent(work_order=wo, machine_id=mach.id, reason_code="BRG-FAIL",
                          description="Bearing failure", category=m.CAT_BREAKDOWN,
                          is_planned=False, start=datetime(2026, 1, 5, 9, 0),
                          duration_min=60.0))
    s.add(m.DowntimeEvent(work_order=wo, machine_id=mach.id, reason_code="PM",
                          description="Planned maintenance",
                          category=m.CAT_PLANNED_MAINTENANCE, is_planned=True,
                          start=datetime(2026, 1, 5, 14, 0), duration_min=30.0))
    s.commit()
    yield s
    s.close()


def test_time_buckets(session):
    df = work_order_frame(session)
    row = df.iloc[0]
    assert row["window_min"] == pytest.approx(510)
    assert row["planned_production_min"] == pytest.approx(480)
    assert row["run_time_min"] == pytest.approx(420)
    assert row["ideal_time_min"] == pytest.approx(400)
    assert row["speed_loss_min"] == pytest.approx(20)
    assert row["quality_loss_min"] == pytest.approx(20)


def test_oee(session):
    g = oee_summary(work_order_frame(session), group_by=["machine"])
    row = g.iloc[0]
    assert row["availability"] == pytest.approx(0.875, abs=1e-4)
    assert row["performance"] == pytest.approx(0.9524, abs=1e-4)
    assert row["quality"] == pytest.approx(0.95, abs=1e-4)
    assert row["oee"] == pytest.approx(0.7917, abs=1e-4)


def test_money_leaks(session):
    leaks = money_leaks(work_order_frame(session))
    by_bucket = dict(zip(leaks["loss_bucket"], leaks["cost"]))
    assert by_bucket["breakdown"] == pytest.approx(120.0)
    assert by_bucket["speed_loss"] == pytest.approx(40.0)
    assert by_bucket["quality_loss"] == pytest.approx(80.0)

    totals = money_leak_totals(work_order_frame(session))
    assert totals["cost"].sum() == pytest.approx(240.0)
    assert totals.iloc[0]["loss_bucket"] == "breakdown"  # ranked


def test_pareto_and_adherence(session):
    pareto = downtime_pareto(downtime_frame(session))
    assert len(pareto) == 1  # planned maintenance excluded
    assert pareto.iloc[0]["reason_code"] == "BRG-FAIL"
    assert pareto.iloc[0]["cum_pct"] == pytest.approx(100.0)

    adh = schedule_adherence(work_order_frame(session))
    assert adh.iloc[0]["on_time_pct"] == 0.0       # 60 min late > 30 min grace
    assert adh.iloc[0]["avg_overrun_min"] == pytest.approx(60.0)


def test_six_big_losses(session):
    losses = six_big_losses(work_order_frame(session))
    by_bucket = dict(zip(losses["loss_bucket"], losses["minutes_lost"]))
    assert by_bucket["breakdown"] == pytest.approx(60.0)
    assert by_bucket["speed_loss"] == pytest.approx(20.0)
    assert by_bucket["quality_loss"] == pytest.approx(20.0)
    assert by_bucket["changeover"] == pytest.approx(0.0)
