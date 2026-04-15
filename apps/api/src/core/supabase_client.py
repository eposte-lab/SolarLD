"""Supabase client factories.

- `get_service_client()` — service role, bypasses RLS; used by workers/agents.
- `get_user_client(jwt)` — authenticated client scoped to a user's JWT; RLS enforced.
"""

from __future__ import annotations

from supabase import Client, create_client

from .config import settings


def get_service_client() -> Client:
    """Service role client (bypasses RLS). Use inside backend workers/agents."""
    if not settings.supabase_service_role_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not set")
    return create_client(
        settings.next_public_supabase_url,
        settings.supabase_service_role_key,
    )


def get_user_client(jwt: str) -> Client:
    """User-scoped client. RLS policies apply against auth_tenant_id()."""
    client = create_client(
        settings.next_public_supabase_url,
        settings.next_public_supabase_anon_key,
    )
    client.postgrest.auth(jwt)
    return client
