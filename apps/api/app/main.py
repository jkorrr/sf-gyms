import logging
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .db import create_engine, session_factory
from .demo_data import DemoExperienceRepository, DemoGymRepository
from .experience_repository import (
    ExperienceGymNotFoundError,
    ExperienceIdempotencyConflictError,
    ExperienceIdempotencyInProgressError,
    SqlAlchemyExperienceRepository,
)
from .repositories import SqlAlchemyGymRepository, response
from .schemas import (
    AcceptedCommand,
    CurrentUser,
    ExperienceReportCreate,
    ExperienceReportPage,
    ExperienceReportSubmission,
    GymDetail,
    GymSearchResponse,
    LeadCreate,
    ReportCreate,
    VenueType,
)
from .security import JwksCache, required_user


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    _configure_logging(settings)
    app.state.engine = create_engine(settings)
    app.state.sessions = session_factory(app.state.engine)
    app.state.demo_repository = DemoGymRepository()
    app.state.demo_experience_repository = DemoExperienceRepository()
    app.state.jwks_cache = JwksCache(settings.supabase_jwks_url) if settings.supabase_jwks_url else None
    yield
    if app.state.engine is not None:
        await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "If-Match", "X-Request-ID"],
        expose_headers=["ETag", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid4())
        response_obj = await call_next(request)
        response_obj.headers["x-request-id"] = request_id
        response_obj.headers["x-content-type-options"] = "nosniff"
        response_obj.headers["referrer-policy"] = "strict-origin-when-cross-origin"
        response_obj.headers["x-frame-options"] = "DENY"
        response_obj.headers["permissions-policy"] = "camera=(), microphone=(), geolocation=()"
        response_obj.headers["content-security-policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        if request.url.path.startswith("/api/v1/me") or request.method != "GET":
            response_obj.headers["cache-control"] = "no-store"
        if settings.production:
            response_obj.headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"
        return response_obj

    @app.get("/healthz", tags=["system"])
    async def healthz():
        return {"status": "ok", "environment": settings.app_env, "demo_mode": settings.demo_mode}

    @app.get("/api/v1/me", response_model=CurrentUser, tags=["auth"])
    async def me(user=Depends(required_user)):
        return CurrentUser(id=user.id, email=user.email, role=user.role, is_demo=user.is_demo)

    @app.get("/api/v1/gyms", response_model=GymSearchResponse, tags=["gyms"])
    async def list_gyms(
        query: str | None = Query(default=None, max_length=100),
        max_monthly: float | None = Query(default=None, ge=0, le=10000),
        venue_type: list[VenueType] | None = Query(default=None),
    ):
        if settings.demo_mode or app.state.sessions is None:
            items = await app.state.demo_repository.search(query=query, max_monthly=max_monthly, venue_types=venue_type)
            return response(items, demo_mode=True)
        async with app.state.sessions() as session:
            async with session.begin():
                items = await SqlAlchemyGymRepository(session).search(query=query, max_monthly=max_monthly, venue_types=venue_type)
            return response(items, demo_mode=False)

    @app.get("/api/v1/gyms/{gym_id}", response_model=GymDetail, tags=["gyms"])
    async def get_gym(gym_id: UUID):
        if settings.demo_mode or app.state.sessions is None:
            gym = await app.state.demo_repository.get(gym_id)
        else:
            async with app.state.sessions() as session:
                async with session.begin():
                    gym = await SqlAlchemyGymRepository(session).get(gym_id)
        if gym is None:
            raise HTTPException(status_code=404, detail="Gym not found")
        return gym

    @app.get(
        "/api/v1/gyms/{gym_id}/experience-reports",
        response_model=ExperienceReportPage,
        tags=["experiences"],
    )
    async def list_experience_reports(
        gym_id: UUID,
        cursor: str | None = Query(default=None, max_length=500),
        limit: int = Query(default=10, ge=1, le=50),
    ):
        try:
            if settings.demo_mode or app.state.sessions is None:
                return await app.state.demo_experience_repository.list_published(
                    gym_id,
                    cursor=cursor,
                    limit=limit,
                )
            async with app.state.sessions() as session:
                async with session.begin():
                    return await SqlAlchemyExperienceRepository(session).list_published(
                        gym_id,
                        cursor=cursor,
                        limit=limit,
                    )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def require_idempotency_key(value: str | None) -> str:
        if not value or len(value) > 128:
            raise HTTPException(status_code=400, detail="Idempotency-Key is required")
        return value

    @app.post(
        "/api/v1/gyms/{gym_id}/experience-reports",
        response_model=ExperienceReportSubmission,
        status_code=status.HTTP_201_CREATED,
        tags=["experiences"],
    )
    async def create_experience_report(
        gym_id: UUID,
        payload: ExperienceReportCreate,
        response_obj: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        user=Depends(required_user),
    ):
        key = require_idempotency_key(idempotency_key)
        try:
            if settings.demo_mode:
                result = await app.state.demo_experience_repository.create_pending(
                    gym_location_id=gym_id,
                    payload=payload,
                    idempotency_key=key,
                )
            else:
                try:
                    user_id = UUID(user.id)
                except ValueError as exc:
                    raise HTTPException(status_code=401, detail="Invalid authenticated user") from exc
                if app.state.sessions is None:
                    raise HTTPException(status_code=503, detail="Review persistence is unavailable")
                async with app.state.sessions() as session:
                    async with session.begin():
                        result = await SqlAlchemyExperienceRepository(session).create_pending(
                            gym_location_id=gym_id,
                            user_id=user_id,
                            payload=payload,
                            idempotency_key=key,
                        )
            if result.already_processed:
                response_obj.status_code = status.HTTP_200_OK
            return result
        except ExperienceGymNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Gym not found") from exc
        except ExperienceIdempotencyConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key was already used with a different request",
            ) from exc
        except ExperienceIdempotencyInProgressError as exc:
            raise HTTPException(status_code=409, detail="An identical request is still processing") from exc

    @app.post("/api/v1/leads", response_model=AcceptedCommand, status_code=status.HTTP_202_ACCEPTED, tags=["commands"])
    async def create_lead(
        payload: LeadCreate,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        user=Depends(required_user),
    ):
        require_idempotency_key(idempotency_key)
        if settings.demo_mode:
            return AcceptedCommand(
                request_id=request.headers.get("x-request-id", str(uuid4())),
                status="accepted",
                message="Demo lead accepted; persistence will use the Supabase idempotency table when configured.",
            )
        raise HTTPException(status_code=501, detail="Lead persistence is scaffolded for the Supabase command service")

    @app.post("/api/v1/reports", response_model=AcceptedCommand, status_code=status.HTTP_202_ACCEPTED, tags=["commands"])
    async def create_report(
        payload: ReportCreate,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        user=Depends(required_user),
    ):
        require_idempotency_key(idempotency_key)
        if settings.demo_mode:
            return AcceptedCommand(
                request_id=request.headers.get("x-request-id", str(uuid4())),
                status="accepted",
                message="Demo report accepted; persistence will use the Supabase idempotency table when configured.",
            )
        raise HTTPException(status_code=501, detail="Report persistence is scaffolded for the Supabase command service")

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        logging.getLogger(__name__).exception("Unhandled request error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
