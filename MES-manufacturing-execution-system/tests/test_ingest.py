from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, select

from mes.db import models as m
from mes.db.session import get_session
from mes.ingest.synthetic import generate
from mes.kpi.frames import work_order_frame
from mes.kpi.oee import oee_summary


@pytest.fixture(scope="module")
def session():
    engine = create_engine("sqlite:///:memory:")
    m.Base.metadata.create_all(engine)
    s = get_session(engine)
    generate(s, months=2, seed=7, end=datetime(2026, 6, 30))
    yield s
    s.close()


def test_generates_all_entities(session):
    assert session.scalar(select(func.count(m.Machine.id))) == 6
    assert session.scalar(select(func.count(m.Product.id))) == 5
    assert session.scalar(select(func.count(m.WorkOrder.id))) > 100
    assert session.scalar(select(func.count(m.DowntimeEvent.id))) > 100


def test_time_identity_holds(session):
    """window == run time + all downtime, within rounding."""
    df = work_order_frame(session)
    resid = (df["window_min"] - df["run_time_min"]
             - df["unplanned_downtime_min"] - df["planned_downtime_min"])
    assert resid.abs().max() < 0.5


def test_oee_sane_and_stories_present(session):
    g = oee_summary(work_order_frame(session), group_by=["machine"])
    assert ((g["oee"] > 0.2) & (g["oee"] < 1.0)).all()
    by_machine = g.set_index("machine")
    # PACK-01 is the speed-loss machine; GRD-01 is the healthy benchmark
    assert by_machine.loc["PACK-01", "performance"] < 0.88
    assert by_machine.loc["GRD-01", "oee"] == by_machine["oee"].max()


def test_quality_counts_consistent(session):
    df = work_order_frame(session)
    assert (df["good_count"] + df["scrap_count"] == df["total_count"]).all()
