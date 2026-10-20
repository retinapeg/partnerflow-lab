"""Request and response contracts mirrored in SCHEMA.md."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Count = Annotated[int, Field(strict=True, ge=0)]
PositiveCount = Annotated[int, Field(strict=True, gt=0)]
Rate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Finite = Annotated[float, Field(allow_inf_nan=False)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Stage(Contract):
    count: Count
    rate_from_previous: Rate
    rate_from_start: Rate


class Stages(Contract):
    data_complete: Stage
    offer: Stage
    accepted: Stage
    drawdown: Stage
    repeat_90d: Stage


class Funnel(Contract):
    partner_id: int
    n: Count
    stages: Stages
    routed_auto_share: Rate


class PowerRequest(Contract):
    baseline_rate: Annotated[float, Field(gt=0, lt=1, allow_inf_nan=False)]
    mde_pp: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    alpha: Annotated[float, Field(gt=0, lt=1, allow_inf_nan=False)] = 0.05
    power: Annotated[float, Field(gt=0, lt=1, allow_inf_nan=False)] = 0.8
    daily_traffic: Annotated[float, Field(gt=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def check_design(self):
        if self.baseline_rate + self.mde_pp / 100 >= 1:
            raise ValueError("baseline_rate + mde_pp / 100 must be below 1")
        if self.alpha >= self.power:
            raise ValueError("alpha must be below power")
        return self


class PowerResult(Contract):
    n_per_arm: PositiveCount
    days_to_run: PositiveCount


class Arm(Contract):
    n: PositiveCount
    conversions: Count

    @model_validator(mode="after")
    def check_conversions(self):
        if self.conversions > self.n:
            raise ValueError("conversions cannot exceed n")
        return self


class AnalyseRequest(Contract):
    control: Arm
    treatment: Arm
    planned_n_per_arm: PositiveCount | None = None


class AnalyseResult(Contract):
    lift_pp: Finite
    ci95: tuple[Finite, Finite]
    p_value: Rate
    prob_treatment_better: Rate
    ready_to_read: bool
    note: str


class Health(Contract):
    status: Literal["ok"]
    version: str


class Error(Contract):
    detail: str | list[dict]
