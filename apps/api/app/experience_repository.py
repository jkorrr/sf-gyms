import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import (
    ExperienceReport,
    ExperienceReportCreate,
    ExperienceReportPage,
    ExperienceReportSubmission,
)


class ExperienceRepositoryError(Exception):
    pass


class ExperienceGymNotFoundError(ExperienceRepositoryError):
    pass


class ExperienceIdempotencyConflictError(ExperienceRepositoryError):
    pass


class ExperienceIdempotencyInProgressError(ExperienceRepositoryError):
    pass


@dataclass(frozen=True)
class _Cursor:
    published_at: datetime
    report_id: UUID


def _encode_cursor(published_at: datetime, report_id: UUID) -> str:
    payload = json.dumps(
        {"published_at": published_at.isoformat(), "report_id": str(report_id)},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(value: str | None) -> _Cursor | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return _Cursor(
            published_at=datetime.fromisoformat(payload["published_at"]),
            report_id=UUID(payload["report_id"]),
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid experience report cursor") from exc


def _report(row: Any) -> ExperienceReport:
    return ExperienceReport(
        id=row["id"],
        gym_location_id=row["gym_location_id"],
        visit_date=row["visit_date"],
        time_bucket=row["time_bucket"],
        relationship=row["relationship"],
        equipment_availability=row["equipment_availability"],
        crowding=row["crowding"],
        cleanliness=row["cleanliness"],
        value_assessment=row["value_assessment"],
        billing_transparency=row["billing_transparency"],
        listing_accuracy=row["listing_accuracy"],
        body=row["body"],
        published_at=row["published_at"],
    )


class SqlAlchemyExperienceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_published(
        self,
        gym_location_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> ExperienceReportPage:
        decoded = _decode_cursor(cursor)
        result = await self.session.execute(
            text(
                """
                SELECT report.id, report.gym_location_id,
                       revision.visit_date, revision.time_bucket, revision.relationship,
                       revision.equipment_availability, revision.crowding,
                       revision.cleanliness, revision.value_assessment,
                       revision.billing_transparency, revision.listing_accuracy,
                       revision.body, revision.published_at
                FROM public.gym_experience_reports report
                JOIN public.gym_experience_revisions revision
                  ON revision.id = report.current_published_revision_id
                 AND revision.report_id = report.id
                WHERE report.gym_location_id = :gym_location_id
                  AND report.status = 'published'
                  AND revision.status = 'published'
                  AND (
                    CAST(:cursor_published_at AS timestamptz) IS NULL
                    OR (revision.published_at, report.id) <
                       (CAST(:cursor_published_at AS timestamptz), CAST(:cursor_report_id AS uuid))
                  )
                ORDER BY revision.published_at DESC, report.id DESC
                LIMIT :query_limit
                """
            ),
            {
                "gym_location_id": gym_location_id,
                "cursor_published_at": decoded.published_at if decoded else None,
                "cursor_report_id": decoded.report_id if decoded else None,
                "query_limit": limit + 1,
            },
        )
        rows = result.mappings().all()
        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        items = [_report(row) for row in visible_rows]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = _encode_cursor(last.published_at, last.id)
        return ExperienceReportPage(items=items, next_cursor=next_cursor, demo_mode=False)

    async def create_pending(
        self,
        *,
        gym_location_id: UUID,
        user_id: UUID,
        payload: ExperienceReportCreate,
        idempotency_key: str,
    ) -> ExperienceReportSubmission:
        canonical_payload = {
            "gym_location_id": str(gym_location_id),
            **payload.model_dump(mode="json"),
        }
        request_hash = hashlib.sha256(
            json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        scope_key = f"experience-report:{user_id}:{idempotency_key}"

        claimed = await self.session.execute(
            text(
                """
                INSERT INTO public.idempotency_keys (
                    scope_key, operation, principal_id, request_hash
                ) VALUES (
                    :scope_key, 'create_experience_report', :principal_id, :request_hash
                )
                ON CONFLICT (scope_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "scope_key": scope_key,
                "principal_id": user_id,
                "request_hash": request_hash,
            },
        )

        if claimed.scalar_one_or_none() is None:
            existing_result = await self.session.execute(
                text(
                    """
                    SELECT request_hash, response_status, response_body
                    FROM public.idempotency_keys
                    WHERE scope_key = :scope_key
                    FOR UPDATE
                    """
                ),
                {"scope_key": scope_key},
            )
            existing = existing_result.mappings().one()
            if existing["request_hash"] != request_hash:
                raise ExperienceIdempotencyConflictError
            if not existing["response_body"]:
                raise ExperienceIdempotencyInProgressError
            return ExperienceReportSubmission(
                **{**existing["response_body"], "already_processed": True}
            )

        gym_result = await self.session.execute(
            text(
                """
                SELECT 1
                FROM public.gym_locations location
                JOIN public.gyms gym ON gym.id = location.gym_id
                WHERE location.id = :gym_location_id AND gym.status = 'published'
                """
            ),
            {"gym_location_id": gym_location_id},
        )
        if gym_result.scalar_one_or_none() is None:
            raise ExperienceGymNotFoundError

        report_result = await self.session.execute(
            text(
                """
                INSERT INTO public.gym_experience_reports (
                    gym_location_id, author_id, status
                ) VALUES (
                    :gym_location_id, :author_id, 'pending'
                )
                RETURNING id
                """
            ),
            {"gym_location_id": gym_location_id, "author_id": user_id},
        )
        report_id = report_result.scalar_one()

        revision_result = await self.session.execute(
            text(
                """
                INSERT INTO public.gym_experience_revisions (
                    report_id, revision_number, visit_date, time_bucket, relationship,
                    equipment_availability, crowding, cleanliness, value_assessment,
                    billing_transparency, listing_accuracy, body, status, submitted_at
                ) VALUES (
                    :report_id, 1, :visit_date, :time_bucket, :relationship,
                    :equipment_availability, :crowding, :cleanliness, :value_assessment,
                    :billing_transparency, :listing_accuracy, :body, 'pending',
                    timezone('utc', now())
                )
                RETURNING id
                """
            ),
            {"report_id": report_id, **payload.model_dump()},
        )
        revision_id = revision_result.scalar_one()
        await self.session.execute(
            text(
                """
                UPDATE public.gym_experience_reports
                SET latest_revision_id = :revision_id
                WHERE id = :report_id
                """
            ),
            {"revision_id": revision_id, "report_id": report_id},
        )

        response = ExperienceReportSubmission(id=report_id)
        await self.session.execute(
            text(
                """
                UPDATE public.idempotency_keys
                SET response_status = 201, response_body = CAST(:response_body AS jsonb)
                WHERE scope_key = :scope_key
                """
            ),
            {
                "scope_key": scope_key,
                "response_body": response.model_dump_json(),
            },
        )
        return response
