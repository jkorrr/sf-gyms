from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

VenueType = Literal[
    "traditional_gym",
    "boutique_fitness",
    "yoga_studio",
    "pilates_barre",
    "martial_arts_boxing",
    "climbing_gym",
    "gymnastics",
    "personal_training",
    "recreation_sports",
    "outdoor_fitness",
    "dance_movement",
]

ExperienceTimeBucket = Literal[
    "early_morning",
    "morning",
    "midday",
    "evening",
    "late_night",
]
ExperienceRelationship = Literal["member", "former_member", "trial", "day_pass", "guest", "other"]
EquipmentAvailability = Literal["available", "short_wait", "long_wait", "not_available"]
CrowdingLevel = Literal["quiet", "moderate", "busy", "packed"]
CleanlinessLevel = Literal["needs_attention", "acceptable", "clean", "exceptionally_clean"]
ValueAssessment = Literal["poor", "fair", "good", "excellent"]
BillingTransparency = Literal["unclear", "partly_clear", "clear"]
ListingAccuracy = Literal["inaccurate", "partly_accurate", "accurate"]
ExperienceStatus = Literal["draft", "pending", "published", "rejected", "withdrawn", "hidden", "removed"]


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
    venue_type: VenueType = "traditional_gym"
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


class ExperienceSignals(BaseModel):
    equipment_availability: EquipmentAvailability | None = None
    crowding: CrowdingLevel | None = None
    cleanliness: CleanlinessLevel | None = None
    value_assessment: ValueAssessment | None = None
    billing_transparency: BillingTransparency | None = None
    listing_accuracy: ListingAccuracy | None = None

    def has_signal(self) -> bool:
        return any(value is not None for value in self.model_dump().values())


class ExperienceReportCreate(ExperienceSignals):
    visit_date: date
    time_bucket: ExperienceTimeBucket | None = None
    relationship: ExperienceRelationship
    body: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_experience(self):
        if self.visit_date > date.today():
            raise ValueError("visit_date cannot be in the future")
        if self.body is not None:
            self.body = self.body.strip()
            if not self.body:
                self.body = None
        if not self.body and not ExperienceSignals(**self.model_dump()).has_signal():
            raise ValueError("Provide at least one structured observation or a written note")
        return self


class ExperienceReport(ExperienceSignals):
    id: UUID
    gym_location_id: UUID
    visit_date: date
    time_bucket: ExperienceTimeBucket | None = None
    relationship: ExperienceRelationship
    body: str | None = None
    published_at: datetime


class ExperienceReportPage(BaseModel):
    items: list[ExperienceReport]
    next_cursor: str | None = None
    demo_mode: bool = False


class ExperienceReportSubmission(BaseModel):
    id: UUID
    status: Literal["pending"] = "pending"
    already_processed: bool = False
