"""JWT validation + current-user dependency for FastAPI.

Supabase signs JWTs with HS256 using `supabase_jwt_secret`.
We validate them locally (no network call) and extract tenant_id
via the `tenant_members` table lookup (cached in Redis).
"""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .config import settings
from .logging import get_logger
from .supabase_client import get_service_client

log = get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


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
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.PyJWTError as exc:
        log.warning("jwt_validation_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    # Resolve tenant membership via service client
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
    except Exception as exc:  # noqa: BLE001
        log.warning("tenant_lookup_failed", user_id=user_id, error=str(exc))

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
