"""JWT validation + current-user dependency for FastAPI.

Supabase newer projects (2024+) sign user JWTs with RS256 using a
per-project asymmetric key pair.  Older projects used HS256 with a
shared secret.  This module handles both transparently:

  1. Try JWKS endpoint  (RS256 / ES256) — for new projects.
  2. Fall back to HS256 shared secret    — for legacy projects.

The JWKS client caches keys in-process (PyJWT default: 5-minute TTL
with automatic rotation on key-id miss).
"""

from __future__ import annotations

import ssl
from functools import lru_cache
from typing import Annotated

import certifi
import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .config import settings
from .logging import get_logger
from .supabase_client import get_service_client

log = get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _get_jwks_client() -> PyJWKClient:
    """Return a cached JWKS client pointed at this project's Supabase.

    Uses certifi's CA bundle so the SSL handshake works on macOS where
    Python does not read from the system keychain by default.
    """
    supabase_url = settings.next_public_supabase_url.rstrip("/")
    jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    log.info("jwks_client_init", url=jwks_url)
    return PyJWKClient(jwks_url, cache_keys=True, ssl_context=ssl_ctx)


def _decode_jwt(token: str) -> dict:
    """Decode a Supabase JWT, supporting both RS256 and HS256.

    Strategy:
      1. Inspect the header algorithm.
      2. RS256 / ES256 → fetch public key from JWKS endpoint.
      3. HS256          → use the shared ``supabase_jwt_secret``.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.DecodeError as exc:
        raise jwt.PyJWTError(f"Malformed token header: {exc}") from exc

    alg = header.get("alg", "")

    if alg in ("RS256", "ES256", "RS384", "ES384", "RS512"):
        # Asymmetric — verify with the project's public key via JWKS.
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=[alg],
            audience="authenticated",
        )

    if alg == "HS256":
        # Legacy shared secret.
        if not settings.supabase_jwt_secret:
            raise jwt.PyJWTError("supabase_jwt_secret not configured for HS256 token")
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )

    raise jwt.PyJWTError(f"Unsupported JWT algorithm: {alg!r}")


class AuthContext(BaseModel):
    """The authenticated caller context."""

    user_id: str
    email: str | None = None
    tenant_id: str | None = None
    role: str = "member"


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthContext:
    """Validate Supabase JWT and resolve tenant membership."""
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    token = creds.credentials
    try:
        payload = _decode_jwt(token)
    except jwt.PyJWTError as exc:
        log.warning(
            "jwt_validation_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        detail = "Invalid token"
        if settings.app_env == "development":
            detail = f"Invalid token ({type(exc).__name__}: {exc})"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
        ) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    # Resolve tenant membership via service client (bypasses RLS).
    tenant_id: str | None = None
    role = "member"
    try:
        sb = get_service_client()
        result = (
            sb.table("tenant_members")
            .select("tenant_id, role")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if result.data:
            tenant_id = result.data[0]["tenant_id"]
            role = result.data[0]["role"]
        else:
            # User authenticated but not bound to any tenant yet — return
            # context with tenant_id=None.  require_tenant() will 403 as needed.
            log.info("tenant_lookup_no_membership", user_id=user_id)
    except Exception as exc:  # noqa: BLE001
        # DB error — this is a 503, not a missing membership.  Returning
        # tenant_id=None here would produce a misleading 403; raise explicitly
        # so callers see an accurate service-unavailable response.
        log.error(
            "tenant_lookup_db_error",
            user_id=user_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tenant membership lookup unavailable — try again shortly",
        ) from exc

    return AuthContext(
        user_id=user_id,
        email=payload.get("email"),
        tenant_id=tenant_id,
        role=role,
    )


CurrentUser = Annotated[AuthContext, Depends(get_current_user)]


def require_tenant(ctx: AuthContext) -> str:
    """Raise 403 if the caller has no tenant binding, else return tenant_id."""
    if not ctx.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not bound to a tenant",
        )
    return ctx.tenant_id
