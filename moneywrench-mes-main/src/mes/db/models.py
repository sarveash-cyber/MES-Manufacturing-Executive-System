"""Canonical MES data model.

Every data source (synthetic generator, Kaggle/UCI adapters, future ERP
exports) maps into this schema; the KPI engine only ever reads this schema.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# Six Big Losses buckets used on DowntimeEvent.category.
# Speed loss and quality losses are derived by the KPI engine, not stored.
CAT_BREAKDOWN = "breakdown"            # availability loss (unplanned)
CAT_CHANGEOVER = "changeover"          # availability loss (setup/adjustment)
CAT_MINOR_STOP = "minor_stop"          # performance loss (short stops)
CAT_PLANNED_MAINTENANCE = "planned_maintenance"  # excluded from planned production time

DOWNTIME_CATEGORIES = (
    CAT_BREAKDOWN,
    CAT_CHANGEOVER,
    CAT_MINOR_STOP,
    CAT_PLANNED_MAINTENANCE,
)


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    work_center: Mapped[str] = mapped_column(String(64), default="main")

    work_orders: Mapped[list[WorkOrder]] = relationship(back_populates="machine")
    cost_rates: Mapped[list[CostRate]] = relationship(back_populates="machine")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    # Seconds of machine time per good unit at rated (nameplate) speed.
    ideal_cycle_time_sec: Mapped[float] = mapped_column(Float)
    unit_material_cost: Mapped[float] = mapped_column(Float, default=0.0)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)

    work_orders: Mapped[list[WorkOrder]] = relationship(back_populates="product")


class CostRate(Base):
    """Fully-burdened cost of one machine-hour (labor + overhead + energy)."""

    __tablename__ = "cost_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    hourly_rate: Mapped[float] = mapped_column(Float)

    machine: Mapped[Machine] = relationship(back_populates="cost_rates")


class Operator(Base):
    """Shop-floor operator (workforce-productivity subsystem)."""

    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    shift: Mapped[str] = mapped_column(String(8), default="DAY")  # DAY | SWING
    skill_level: Mapped[int] = mapped_column(Integer, default=3)  # 1..5
    hourly_rate: Mapped[float] = mapped_column(Float, default=30.0)

    work_orders: Mapped[list[WorkOrder]] = relationship(back_populates="operator")


class EnergyReading(Base):
    """Energy consumed by one work order (energy subsystem)."""

    __tablename__ = "energy_readings"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    kwh: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)

    work_order: Mapped[WorkOrder] = relationship()


class InventorySnapshot(Base):
    """Month-end on-hand inventory per product (inventory subsystem)."""

    __tablename__ = "inventory_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    date: Mapped[datetime] = mapped_column(DateTime, index=True)
    qty_on_hand: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[float] = mapped_column(Float)


class WorkOrder(Base):
    """A scheduled production order: the planned vs actual comparison unit."""

    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)

    scheduled_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime)
    planned_qty: Mapped[int] = mapped_column(Integer)

    actual_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="completed")

    # traceability / genealogy + workforce
    batch_number: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"),
                                                    nullable=True, index=True)

    machine: Mapped[Machine] = relationship(back_populates="work_orders")
    product: Mapped[Product] = relationship(back_populates="work_orders")
    operator: Mapped[Operator | None] = relationship(back_populates="work_orders")
    runs: Mapped[list[ProductionRun]] = relationship(back_populates="work_order")
    downtime_events: Mapped[list[DowntimeEvent]] = relationship(back_populates="work_order")


class ProductionRun(Base):
    __tablename__ = "production_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime] = mapped_column(DateTime)
    total_count: Mapped[int] = mapped_column(Integer)

    work_order: Mapped[WorkOrder] = relationship(back_populates="runs")
    quality_records: Mapped[list[QualityRecord]] = relationship(back_populates="run")


class QualityRecord(Base):
    __tablename__ = "quality_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("production_runs.id"), index=True)
    good_count: Mapped[int] = mapped_column(Integer)
    scrap_count: Mapped[int] = mapped_column(Integer, default=0)
    rework_count: Mapped[int] = mapped_column(Integer, default=0)
    defect_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    run: Mapped[ProductionRun] = relationship(back_populates="quality_records")


class DowntimeEvent(Base):
    __tablename__ = "downtime_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    reason_code: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str] = mapped_column(String(256), default="")
    category: Mapped[str] = mapped_column(String(32), index=True)  # DOWNTIME_CATEGORIES
    is_planned: Mapped[bool] = mapped_column(Boolean, default=False)
    start: Mapped[datetime] = mapped_column(DateTime)
    duration_min: Mapped[float] = mapped_column(Float)

    work_order: Mapped[WorkOrder] = relationship(back_populates="downtime_events")
