from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import Settings


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    return url


def create_engine(settings: Settings) -> AsyncEngine | None:
    if not settings.database_url:
        return None
    return create_async_engine(
        normalize_database_url(settings.database_url),
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=10,
        pool_size=5,
        max_overflow=5,
    )


def session_factory(engine: AsyncEngine | None) -> async_sessionmaker[AsyncSession] | None:
    if engine is None:
        return None
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(factory: async_sessionmaker[AsyncSession] | None) -> AsyncIterator[AsyncSession]:
    if factory is None:
        raise RuntimeError("A database session was requested without DATABASE_URL")
    async with factory() as session:
        async with session.begin():
            yield session
