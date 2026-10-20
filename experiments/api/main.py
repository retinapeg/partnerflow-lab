"""Run after generating data: python -m uvicorn api.main:app."""

from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException

from analysis.ab import analyse
from analysis.power import calculate_power
from api.models import (
    AnalyseRequest, AnalyseResult, Error, Funnel, Health, PowerRequest, PowerResult,
)

ROOT = Path(__file__).resolve().parents[1]


def create_app(events_path: Path = ROOT / "data/events.parquet") -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        # Load a fixed snapshot once, with no simulator or sampler in requests.
        app.state.events = pd.read_parquet(events_path)
        yield

    app = FastAPI(title="Partner Flow Lab", version="0.1.0", lifespan=lifespan)
    errors = {422: {"model": Error}}

    @app.get("/health", response_model=Health)
    def health():
        return {"status": "ok", "version": app.version}

    @app.get("/v1/funnel/{partner_id}", response_model=Funnel,
             responses={404: {"model": Error}, **errors})
    def funnel(partner_id: int):
        events = app.state.events
        partner = events.loc[events.partner_id == partner_id]
        if partner.empty:
            raise HTTPException(status_code=404, detail="Unknown partner")
        n = len(partner)
        previous = n
        stages = {}
        for name in ("data_complete", "offer", "accepted", "drawdown", "repeat_90d"):
            column = name if name == "repeat_90d" else f"stage_{name}"
            count = int(partner[column].sum())
            stages[name] = {
                "count": count,
                "rate_from_previous": count / previous if previous else 0.0,
                "rate_from_start": count / n,
            }
            previous = count
        completed = stages["data_complete"]["count"]
        return {
            "partner_id": partner_id, "n": n, "stages": stages,
            "routed_auto_share": float(partner.routed_auto.sum() / completed) if completed else 0.0,
        }

    @app.post("/v1/experiments/power", response_model=PowerResult, responses=errors)
    def power(body: PowerRequest):
        return calculate_power(**body.model_dump())

    @app.post("/v1/experiments/analyse", response_model=AnalyseResult, responses=errors)
    def analysis(body: AnalyseRequest):
        return analyse(**body.model_dump())

    return app


app = create_app()
