"""FastAPI application: KPI endpoints, dashboard, agent Q&A.

Run:  uvicorn mes.api.main:app --reload --app-dir src
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from mes.db.session import get_session, init_db
from mes.kpi.frames import downtime_frame, work_order_frame
from mes.kpi.losses import downtime_pareto, schedule_adherence, six_big_losses
from mes.kpi.money import money_leak_totals, money_leaks
from mes.kpi.oee import oee_summary

app = FastAPI(title="MES Analytics", version="0.1.0")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "web" / "templates"))

ALLOWED_GROUPS = {"machine", "product", "month", "work_center", "plant"}


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _records(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%dT%H:%M:%S")
    out = df.to_dict(orient="records")
    for row in out:
        for k, v in row.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                row[k] = None
    return out


def _parse_groups(group_by: str) -> list[str]:
    groups = [g.strip() for g in group_by.split(",") if g.strip()]
    bad = set(groups) - ALLOWED_GROUPS
    if bad:
        raise HTTPException(422, f"invalid group_by {sorted(bad)}; allowed: {sorted(ALLOWED_GROUPS)}")
    return groups


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"invalid date: {value!r} (use ISO, e.g. 2026-01-01)")


def _wo_frame(start: str | None, end: str | None) -> pd.DataFrame:
    session = get_session()
    try:
        df = work_order_frame(session, _parse_date(start), _parse_date(end))
    finally:
        session.close()
    if not df.empty:
        df["plant"] = "PLANT"  # pseudo-group for plant-wide aggregates
    return df


@app.get("/kpi/oee")
def kpi_oee(group_by: str = Query("machine"), start: str | None = None,
            end: str | None = None) -> list[dict]:
    return _records(oee_summary(_wo_frame(start, end), _parse_groups(group_by)))


@app.get("/kpi/losses")
def kpi_losses(group_by: str = Query("machine"), start: str | None = None,
               end: str | None = None) -> list[dict]:
    return _records(six_big_losses(_wo_frame(start, end), _parse_groups(group_by)))


@app.get("/kpi/downtime-pareto")
def kpi_pareto(by: str = Query("reason_code", pattern="^(reason_code|category|machine)$"),
               start: str | None = None, end: str | None = None) -> list[dict]:
    session = get_session()
    try:
        dt = downtime_frame(session, _parse_date(start), _parse_date(end))
    finally:
        session.close()
    return _records(downtime_pareto(dt, by=by))


@app.get("/kpi/schedule-adherence")
def kpi_adherence(group_by: str = Query("machine"), start: str | None = None,
                  end: str | None = None) -> list[dict]:
    return _records(schedule_adherence(_wo_frame(start, end), _parse_groups(group_by)))


@app.get("/kpi/money-leaks")
def kpi_money(group_by: str = Query("machine"), start: str | None = None,
              end: str | None = None, totals: bool = False) -> list[dict]:
    df = _wo_frame(start, end)
    if totals:
        return _records(money_leak_totals(df))
    return _records(money_leaks(df, _parse_groups(group_by)))


class AnalyzeRequest(BaseModel):
    question: str


@app.post("/analyze")
async def analyze(req: AnalyzeRequest) -> dict:
    """Ask the multi-agent analyst a question about the production history."""
    from mes.agents.orchestrator import ask  # lazy: needs ANTHROPIC_API_KEY

    try:
        answer = await ask(req.question)
    except Exception as e:  # surface agent/auth errors as a readable payload
        raise HTTPException(502, f"agent error: {e}")
    return {"question": req.question, "answer": answer}


@app.post("/ingest/synthetic")
def ingest_synthetic(months: int = 12) -> dict:
    from mes.db.models import Base
    from mes.db.session import get_engine
    from mes.ingest.synthetic import generate

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        return generate(session, months=months)
    finally:
        session.close()


@app.get("/kpi/subsystems")
def kpi_subsystems() -> dict:
    """Full subsystem KPI matrix (production, maintenance, quality, inventory,
    energy, workforce, compliance)."""
    from mes.kpi.subsystems import subsystem_matrix

    session = get_session()
    try:
        return subsystem_matrix(session)
    finally:
        session.close()


@app.get("/api/agents")
def api_agents() -> list[dict]:
    """The agent crew with its live (configurable) models — see config/agents.yaml."""
    from mes.agents.definitions import crew_info

    return crew_info()


@app.get("/api/advisor/kb")
def advisor_kb() -> dict:
    """Sales Advisor knowledge base: levers, pain points, sector packs +
    live demo-plant baselines for prefill."""
    from mes.api.advisor_kb import advisor_payload

    session = get_session()
    try:
        return advisor_payload(session)
    finally:
        session.close()


class SmeChatRequest(BaseModel):
    session_id: str
    message: str
    page: str = ""


@app.post("/sme/chat")
async def sme_chat(req: SmeChatRequest):
    """Converse with the Manufacturing SME agent. Streams SSE `data:` lines
    with JSON payloads {"text": chunk}, terminated by {"done": true}."""
    import json as _json

    from fastapi.responses import StreamingResponse

    from mes.agents.sme import chat as sme_chat_fn

    async def gen():
        try:
            async for chunk in sme_chat_fn(req.session_id, req.message, req.page):
                yield f"data: {_json.dumps({'text': chunk})}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'error': str(e)})}\n\n"
        yield f"data: {_json.dumps({'done': True})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.delete("/sme/chat/{session_id}")
async def sme_reset(session_id: str) -> dict:
    """End a SME chat session (frees the underlying agent client)."""
    from mes.agents.sme import reset

    return {"reset": await reset(session_id)}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"page": "dashboard"})


@app.get("/subsystems", response_class=HTMLResponse)
def subsystems_page(request: Request):
    return templates.TemplateResponse(request, "subsystems.html", {"page": "subsystems"})


@app.get("/advisor", response_class=HTMLResponse)
def advisor_page(request: Request):
    return templates.TemplateResponse(request, "advisor.html", {"page": "advisor"})


@app.get("/data", response_class=HTMLResponse)
def data_page(request: Request):
    from mes.api.datasources import catalogue

    session = get_session()
    try:
        ctx = catalogue(session)
    finally:
        session.close()
    ctx["page"] = "data"
    return templates.TemplateResponse(request, "data.html", ctx)


@app.get("/playground", response_class=HTMLResponse)
def playground(request: Request):
    return templates.TemplateResponse(request, "playground.html", {"page": "playground"})


@app.get("/agents", response_class=HTMLResponse)
def agents_page(request: Request):
    return templates.TemplateResponse(request, "agents.html", {"page": "agents"})
