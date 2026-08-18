from collections.abc import Mapping
from datetime import UTC
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import GymDetail, GymSearchResponse, GymSummary, PriceSnapshot


def _summary(row: Mapping[str, Any]) -> GymSummary:
    return GymSummary(
        id=row["id"],
        name=row["name"],
        address=row["address"],
        neighborhood=row.get("neighborhood"),
        latitude=float(row["latitude"]),
        longitude=float(row["longitude"]),
        gym_type=row.get("gym_type") or "Gym",
        is_open_24_7=bool(row.get("is_open_24_7", False)),
        amenities=list(row.get("amenities") or []),
        monthly_price=row.get("monthly_price"),
        annual_fee=row.get("annual_fee"),
        day_pass_price=row.get("day_pass_price"),
        price_freshness=row.get("price_freshness") or "unknown",
        updated_at=row.get("updated_at"),
    )


class SqlAlchemyGymRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def search(self, query: str | None = None, max_monthly: float | None = None) -> list[GymSummary]:
        result = await self.session.execute(
            text(
                """
                WITH current_prices AS (
                    SELECT DISTINCT ON (pp.gym_location_id, pp.plan_type)
                        pp.gym_location_id, pp.plan_type, pp.amount,
                        COALESCE(pa.annual_fee, pp.annual_fee) AS annual_fee,
                        pa.freshness
                    FROM public.price_plans pp
                    JOIN public.price_assertions pa ON pa.price_plan_id = pp.id
                    WHERE pa.status = 'published'
                    ORDER BY pp.gym_location_id, pp.plan_type, pa.verified_at DESC NULLS LAST
                )
                SELECT gl.id, g.name, gl.address, gl.neighborhood,
                       ST_Y(gl.coordinates::geometry) AS latitude,
                       ST_X(gl.coordinates::geometry) AS longitude,
                       g.gym_type, gl.is_open_24_7,
                       ARRAY_REMOVE(ARRAY_AGG(DISTINCT a.slug), NULL) AS amenities,
                       MAX(CASE WHEN cp.plan_type = 'monthly' THEN cp.amount END) AS monthly_price,
                       MAX(CASE WHEN cp.plan_type = 'monthly' THEN cp.annual_fee END) AS annual_fee,
                       MAX(CASE WHEN cp.plan_type = 'day_pass' THEN cp.amount END) AS day_pass_price,
                       MAX(cp.freshness) AS price_freshness,
                       GREATEST(g.updated_at, gl.updated_at) AS updated_at
                FROM public.gym_locations gl
                JOIN public.gyms g ON g.id = gl.gym_id
                LEFT JOIN public.gym_location_amenities gla ON gla.gym_location_id = gl.id
                LEFT JOIN public.amenities a ON a.id = gla.amenity_id
                LEFT JOIN current_prices cp ON cp.gym_location_id = gl.id
                WHERE g.status = 'published'
                  AND (:query IS NULL OR g.name ILIKE :like_query OR gl.neighborhood ILIKE :like_query)
                GROUP BY gl.id, g.name, gl.address, gl.neighborhood, gl.coordinates,
                         g.gym_type, gl.is_open_24_7, g.updated_at, gl.updated_at
                HAVING (:max_monthly IS NULL OR MAX(CASE WHEN cp.plan_type = 'monthly' THEN cp.amount END) <= :max_monthly)
                ORDER BY monthly_price NULLS LAST, g.name
                LIMIT 100
                """
            ),
            {"query": query, "like_query": f"%{query}%" if query else None, "max_monthly": max_monthly},
        )
        return [_summary(cast(Mapping[str, Any], row)) for row in result.mappings().all()]

    async def get(self, gym_id: UUID) -> GymDetail | None:
        result = await self.session.execute(
            text(
                """
                SELECT gl.id, g.name, gl.address, gl.neighborhood,
                       ST_Y(gl.coordinates::geometry) AS latitude,
                       ST_X(gl.coordinates::geometry) AS longitude,
                       g.gym_type, g.description, g.website_url, g.phone,
                       gl.is_open_24_7,
                       ARRAY_REMOVE(ARRAY_AGG(DISTINCT a.slug), NULL) AS amenities,
                       GREATEST(g.updated_at, gl.updated_at) AS updated_at
                FROM public.gym_locations gl
                JOIN public.gyms g ON g.id = gl.gym_id
                LEFT JOIN public.gym_location_amenities gla ON gla.gym_location_id = gl.id
                LEFT JOIN public.amenities a ON a.id = gla.amenity_id
                WHERE gl.id = :gym_id AND g.status = 'published'
                GROUP BY gl.id, g.name, gl.address, gl.neighborhood, gl.coordinates,
                         g.gym_type, g.description, g.website_url, g.phone,
                         gl.is_open_24_7, g.updated_at, gl.updated_at
                """
            ),
            {"gym_id": gym_id},
        )
        row = result.mappings().first()
        if row is None:
            return None
        summary = _summary(cast(Mapping[str, Any], row))
        prices_result = await self.session.execute(
            text(
                """
                SELECT pp.plan_type, pp.amount, pp.billing_interval,
                       pp.initiation_fee,
                       COALESCE(pa.annual_fee, pp.annual_fee) AS annual_fee,
                       pa.verified_at, pa.freshness
                FROM public.price_plans pp
                JOIN public.price_assertions pa ON pa.price_plan_id = pp.id
                WHERE pp.gym_location_id = :gym_id AND pa.status = 'published'
                ORDER BY pa.verified_at DESC NULLS LAST
                """
            ),
            {"gym_id": gym_id},
        )
        prices = [PriceSnapshot(**price) for price in prices_result.mappings().all()]
        return GymDetail(
            **summary.model_dump(),
            description=row.get("description"),
            website_url=row.get("website_url"),
            phone=row.get("phone"),
            prices=prices,
        )


def response(items: list[GymSummary], demo_mode: bool) -> GymSearchResponse:
    from datetime import datetime

    return GymSearchResponse(items=items, generated_at=datetime.now(UTC), demo_mode=demo_mode)
