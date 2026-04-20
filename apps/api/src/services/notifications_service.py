"""In-app notifications — tiny helper around the ``notifications`` table.

Intended usage from agents / routes:

    from ..services.notifications_service import notify

    await notify(
        tenant_id=tenant_id,
        title="Nuovo contratto firmato",
        severity="success",
        body=f"Lead {business_name} ha firmato",
        href=f"/leads/{lead_id}",
        metadata={"lead_id": lead_id},
    )

Omitting ``user_id`` broadcasts to every member of the tenant. The
RLS policy in migration 0017 restricts SELECT to the intended
recipient so a member never sees another tenant's bell.
"""

from __future__ import annotations

from typing import Any

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)


SUPPORTED_SEVERITIES: frozenset[str] = frozenset(
    {"info", "success", "warning", "error"}
)


async def notify(
    *,
    tenant_id: str,
    title: str,
    body: str | None = None,
    severity: str = "info",
    href: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Insert a notification row. Best-effort — returns None on failure."""
    if severity not in SUPPORTED_SEVERITIES:
        severity = "info"
    try:
        sb = get_service_client()
        res = (
            sb.table("notifications")
            .insert(
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "severity": severity,
                    "title": title,
                    "body": body,
                    "href": href,
                    "metadata": metadata or {},
                }
            )
            .execute()
        )
        return (res.data or [{}])[0]
    except Exception as exc:  # noqa: BLE001
        log.warning("notifications.insert_failed", title=title, err=str(exc))
        return None
