from decimal import Decimal
from uuid import UUID

from pydantic import HttpUrl

from .schemas import GymDetail, GymSummary, PriceSnapshot

DEMO_GYMS: list[GymDetail] = [
    GymDetail(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        name="Mission Strength Co.",
        address="2200 Mission Street, San Francisco, CA",
        neighborhood="Mission",
        latitude=37.7614,
        longitude=-122.4181,
        gym_type="Strength gym",
        is_open_24_7=True,
        amenities=["free weights", "squat racks", "showers", "24/7 access"],
        monthly_price=Decimal("89"),
        day_pass_price=Decimal("20"),
        price_freshness="verified",
        description="A welcoming strength-focused gym with serious equipment and a neighborhood feel.",
        website_url=HttpUrl("https://example.com/mission-strength"),
        phone="(415) 555-0142",
        hours={"Monday-Friday": "Open 24 hours", "Saturday-Sunday": "Open 24 hours"},
        prices=[
            PriceSnapshot(plan_type="monthly", amount=Decimal("89"), billing_interval="month", initiation_fee=Decimal("0"), freshness="verified"),
            PriceSnapshot(plan_type="day_pass", amount=Decimal("20"), freshness="verified"),
        ],
    ),
    GymDetail(
        id=UUID("22222222-2222-4222-8222-222222222222"),
        name="Hayes Valley Movement",
        address="480 Hayes Street, San Francisco, CA",
        neighborhood="Hayes Valley",
        latitude=37.7765,
        longitude=-122.4248,
        gym_type="Boutique fitness",
        is_open_24_7=False,
        amenities=["classes", "sauna", "showers", "yoga"],
        monthly_price=Decimal("139"),
        day_pass_price=Decimal("30"),
        price_freshness="gym_reported",
        description="Small-group classes and open-gym hours in a bright, calm studio.",
        website_url=HttpUrl("https://example.com/hayes-movement"),
        phone="(415) 555-0187",
        hours={"Monday-Friday": "6:00 AM–9:00 PM", "Saturday-Sunday": "8:00 AM–6:00 PM"},
        prices=[
            PriceSnapshot(plan_type="monthly", amount=Decimal("139"), billing_interval="month", initiation_fee=Decimal("25"), freshness="gym_reported"),
            PriceSnapshot(plan_type="day_pass", amount=Decimal("30"), freshness="gym_reported"),
        ],
    ),
    GymDetail(
        id=UUID("33333333-3333-4333-8333-333333333333"),
        name="North Beach Community Gym",
        address="1450 Stockton Street, San Francisco, CA",
        neighborhood="North Beach",
        latitude=37.7999,
        longitude=-122.4089,
        gym_type="Community gym",
        is_open_24_7=False,
        amenities=["cardio", "free weights", "basketball", "student discount"],
        monthly_price=Decimal("49"),
        day_pass_price=Decimal("12"),
        price_freshness="stale",
        description="An affordable local option with broad equipment and court access.",
        website_url=HttpUrl("https://example.com/north-beach-gym"),
        phone="(415) 555-0199",
        hours={"Monday-Friday": "5:00 AM–10:00 PM", "Saturday-Sunday": "7:00 AM–8:00 PM"},
        prices=[
            PriceSnapshot(plan_type="monthly", amount=Decimal("49"), billing_interval="month", initiation_fee=Decimal("0"), freshness="stale"),
            PriceSnapshot(plan_type="day_pass", amount=Decimal("12"), freshness="stale"),
        ],
    ),
]


class DemoGymRepository:
    async def search(self, query: str | None = None, max_monthly: float | None = None) -> list[GymSummary]:
        items = DEMO_GYMS
        if query:
            needle = query.casefold()
            items = [
                gym for gym in items
                if needle in gym.name.casefold()
                or needle in (gym.neighborhood or "").casefold()
                or needle in gym.address.casefold()
            ]
        if max_monthly is not None:
            items = [gym for gym in items if gym.monthly_price is not None and gym.monthly_price <= max_monthly]
        return [GymSummary.model_validate(gym) for gym in items]

    async def get(self, gym_id: UUID) -> GymDetail | None:
        return next((gym for gym in DEMO_GYMS if gym.id == gym_id), None)
