from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class PriceSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plan_type: Literal["monthly", "annual", "day_pass", "trial", "class_pack"]
    amount: Decimal | None = None
    billing_interval: str | None = None
    initiation_fee: Decimal | None = None
    annual_fee: Decimal | None = None
    verified_at: datetime | None = None
    freshness: Literal["verified", "gym_reported", "user_reported", "stale", "unknown"]


class GymSummary(BaseModel):
    id: UUID
    name: str
    address: str
    neighborhood: str | None = None
    latitude: float
    longitude: float
    gym_type: str
    is_open_24_7: bool = False
    amenities: list[str] = Field(default_factory=list)
    monthly_price: Decimal | None = None
    annual_fee: Decimal | None = None
    day_pass_price: Decimal | None = None
    price_freshness: str = "unknown"
    updated_at: datetime | None = None


class GymDetail(GymSummary):
    description: str | None = None
    website_url: HttpUrl | None = None
    phone: str | None = None
    hours: dict[str, str] = Field(default_factory=dict)
    prices: list[PriceSnapshot] = Field(default_factory=list)


class GymSearchResponse(BaseModel):
    items: list[GymSummary]
    next_cursor: str | None = None
    generated_at: datetime
    demo_mode: bool = False


class CurrentUser(BaseModel):
    id: str
    email: str | None = None
    role: Literal["user", "owner", "moderator", "admin"] = "user"
    is_demo: bool = False


class LeadCreate(BaseModel):
    gym_location_id: UUID
    intent: Literal["tour", "trial", "call", "website"]
    email: str | None = Field(default=None, max_length=320)
    note: str | None = Field(default=None, max_length=1000)


class ReportCreate(BaseModel):
    gym_location_id: UUID
    field_name: Literal["price", "hours", "amenities", "closed", "duplicate", "other"]
    note: str | None = Field(default=None, max_length=1000)


class AcceptedCommand(BaseModel):
    request_id: str
    status: Literal["accepted", "already_processed"]
    message: str
