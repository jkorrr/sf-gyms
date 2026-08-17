import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request, status

from .config import Settings


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str | None
    role: str = "user"
    is_demo: bool = False


class JwksCache:
    """Shared-key cache with a lock to avoid a refresh stampede under concurrency."""

    def __init__(self, url: str, ttl_seconds: int = 900):
        self.url = url
        self.ttl_seconds = ttl_seconds
        self._keys: dict[str, Any] | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get_key(self, kid: str) -> Any:
        if self._keys is None or time.monotonic() >= self._expires_at:
            async with self._lock:
                if self._keys is None or time.monotonic() >= self._expires_at:
                    async with httpx.AsyncClient(timeout=5) as client:
                        response = await client.get(self.url)
                        response.raise_for_status()
                        self._keys = {key["kid"]: key for key in response.json()["keys"]}
                    self._expires_at = time.monotonic() + self.ttl_seconds
        if self._keys is None or kid not in self._keys:
            raise HTTPException(status_code=401, detail="Unknown signing key")
        return jwt.algorithms.RSAAlgorithm.from_jwk(self._keys[kid])


def _bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    return token if scheme.lower() == "bearer" and token else None


async def optional_user(request: Request, settings: Settings) -> AuthenticatedUser | None:
    token = _bearer_token(request)
    if not token:
        return AuthenticatedUser(id="demo-user", email="demo@example.com", is_demo=True) if settings.demo_mode else None
    if not settings.supabase_jwks_url or not settings.supabase_jwt_issuer:
        if settings.demo_mode:
            return AuthenticatedUser(id="demo-user", email="demo@example.com", is_demo=True)
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    try:
        header = jwt.get_unverified_header(token)
        key = await request.app.state.jwks_cache.get_key(header["kid"])
        claims = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience="authenticated",
            issuer=settings.supabase_jwt_issuer,
            options={"require": ["sub", "exp", "iat"]},
        )
    except (jwt.PyJWTError, KeyError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=401, detail="Invalid authentication token") from exc
    app_metadata = claims.get("app_metadata") or {}
    role = app_metadata.get("role", "user")
    if role not in {"user", "owner", "moderator", "admin"}:
        role = "user"
    return AuthenticatedUser(id=claims["sub"], email=claims.get("email"), role=role)


async def required_user(request: Request) -> AuthenticatedUser:
    user = await optional_user(request, request.app.state.settings)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user
