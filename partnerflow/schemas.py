"""Pydantic models: the shapes of the JSON the API accepts and returns.

FastAPI reads these classes to (1) validate incoming JSON before your function
runs, (2) shape the JSON that goes back and (3) build the /docs page.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProductName = Literal["term_loan", "credit_line", "invoice_finance"]
ApplicationStatus = Literal["approved", "referred", "declined"]


# --- Customers --------------------------------------------------------------

class CustomerCreate(BaseModel):
    """Body of POST /customers."""

    # extra="forbid": a misspelt field name is an error, not silently ignored.
    model_config = ConfigDict(extra="forbid")

    business_name: str = Field(min_length=2, max_length=120, examples=["Harbour Street Bakery Ltd"])
    contact_email: str = Field(
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        examples=["owner@harbourstreetbakery.example"],
    )
    annual_turnover: int = Field(gt=0, le=1_000_000_000, description="Whole GBP per year.", examples=[240000])
    trading_months: int = Field(ge=0, le=1200, description="How long the business has traded.", examples=[36])


class Customer(BaseModel):
    """What the API returns for a customer."""

    id: int
    partner_id: str
    business_name: str
    contact_email: str
    annual_turnover: int
    trading_months: int
    created_at: str


# --- Applications -----------------------------------------------------------

class ApplicationCreate(BaseModel):
    """Body of POST /applications."""

    model_config = ConfigDict(extra="forbid")

    customer_id: int = Field(gt=0, examples=[1])
    product: ProductName = Field(examples=["term_loan"])
    # Money is whole pounds stored as an integer. Floats cannot represent
    # every decimal amount exactly, so they are avoided for money.
    amount: int = Field(gt=0, le=10_000_000, description="Requested amount in whole GBP.", examples=[20000])
    term_months: int = Field(ge=1, le=60, examples=[12])
    purpose: str | None = Field(default=None, max_length=200, examples=["New oven"])


class Application(BaseModel):
    """What the API returns for an application."""

    id: int
    customer_id: int
    partner_id: str
    product: ProductName
    amount: int
    term_months: int
    purpose: str | None
    status: ApplicationStatus
    decision_reason: str | None
    release_version: str
    created_at: str


# --- Partners, health, errors -----------------------------------------------

class ProductLimits(BaseModel):
    product: str
    min_amount: int
    max_amount: int


class Partner(BaseModel):
    """A simulated lending partner. The API key is deliberately not included."""

    id: str
    name: str
    channel: str


class PartnerList(BaseModel):
    partners: list[Partner]
    products: list[ProductLimits]


class Health(BaseModel):
    status: str
    release_version: str | None = None
    database: str | None = None


class ErrorDetail(BaseModel):
    code: str = Field(description="Stable machine-readable reason, e.g. customer_not_found.")
    message: str
    request_id: str = Field(description="Quote this to find the request in the log.")
    details: list[dict] | None = None


class ErrorResponse(BaseModel):
    """Every error from this API has this shape."""

    error: ErrorDetail


# --- Metrics ----------------------------------------------------------------

class RequestTotals(BaseModel):
    total: int
    succeeded: int
    client_errors: int
    server_errors: int
    error_rate: float | None
    latency_ms_p50: float | None
    latency_ms_p95: float | None


class PartnerMetrics(BaseModel):
    partner_id: str
    total: int
    errors: int
    error_rate: float | None
    latency_ms_p95: float | None


class ApplicationTotals(BaseModel):
    total: int
    approved: int
    referred: int
    declined: int
    approval_rate: float | None


class ReleaseState(BaseModel):
    release: Literal["A", "B"]
    hotfix: bool
    version: str
    notes: str


class Metrics(BaseModel):
    run_label: str | None
    release: ReleaseState
    active_faults: list[str]
    requests: RequestTotals
    by_partner: list[PartnerMetrics]
    by_status_code: dict[str, int]
    by_failure_reason: dict[str, int]
    applications: ApplicationTotals


# --- Lab controls -----------------------------------------------------------

class ReleaseChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release: Literal["A", "B"]


class HotfixChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class BandChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_amount: int = Field(gt=0)
    max_amount: int = Field(gt=0)


class FaultChange(BaseModel):
    """Body of PUT /lab/faults/{name}. Only send the settings you want to change."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    partner: Literal["all", "alder", "birch", "cedar"] | None = None
    every_nth: int | None = Field(default=None, ge=1, le=1000)
    delay_ms: int | None = Field(default=None, ge=0, le=5000)
    limit: int | None = Field(default=None, ge=1, le=100000)
    window_seconds: int | None = Field(default=None, ge=1, le=3600)
    share: float | None = Field(default=None, ge=0.0, le=1.0)


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keep_config: bool = False


class RequestLogEntry(BaseModel):
    id: int
    timestamp: str
    request_id: str
    partner_id: str | None
    method: str
    endpoint: str
    path: str
    status_code: int
    latency_ms: float
    failure_reason: str | None
    release_version: str
    run_label: str | None
    metadata: dict


class WebhookDelivery(BaseModel):
    id: int
    timestamp: str
    application_id: int
    partner_id: str
    event: str
    attempts: int
    status: Literal["delivered", "failed"]
    last_status_code: int | None
    failure_reason: str | None
