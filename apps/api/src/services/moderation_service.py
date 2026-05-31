"""Trial-moderation gate helpers (migration 0147 — gate moved up).

History: migration 0145 hid every un-released lead from a moderated
tenant at the DB level (the ``leads_select`` RLS policy). Because the
``routes/leads.py`` endpoints read through the **service role** (which
bypasses RLS), this module re-imposed the same row-hiding at the API
layer (``apply_released_filter`` / ``assert_lead_visible``).

Revised requirement (pre go-live): the moderated tenant must SEE and
operate its own contatti normally — browse them, open the scheda of a
sent ID, fetch its bolletta, etc. So migration 0147 relaxed the RLS gate
back to plain tenant-scoping and moved the moderation gate UP to a single
place: the dashboard's lead-surface queries
(``apps/dashboard/src/lib/data/leads.ts``), which withhold the *lead*
classification (the /leads list, the hot-leads widgets and KPI) for an
engaged-but-un-promoted contatto. Everything else the tenant sees stays
visible.

To match that posture the two row-hiding helpers below are now **no-ops**
— the API no longer hides un-promoted contatti, so a service-role
single-lead read/action (bolletta card, GDPR fetch, follow-up draft, …)
never 404s on a contatto the tenant can legitimately see. The call sites
in ``routes/leads.py`` are intentionally left in place: they document
where lead reads happen and keep a single switch to flip should the gate
ever need to return to the API layer.

``is_moderated`` is kept as a truthful helper (still used to label/log).
"""

from __future__ import annotations

from typing import Any

from ..core.logging import get_logger
from .appointment_service import is_tenant_moderated

log = get_logger(__name__)


def is_moderated(sb: Any, tenant_id: str) -> bool:
    """True when the tenant is under trial moderation (fail-open False)."""
    return is_tenant_moderated(sb, tenant_id)


def apply_released_filter(query: Any, sb: Any, tenant_id: str) -> Any:
    """No-op since migration 0147 — the lead-surface gate lives in the
    dashboard now (``lib/data/leads.ts``), not at the API/RLS layer. The
    moderated tenant sees and operates its contatti like any other tenant;
    only their promotion to a *lead* is withheld, and that is enforced in
    the dashboard's lead-surface queries. Returns the query unchanged.
    """
    return query


def assert_lead_visible(sb: Any, tenant_id: str, lead_id: str) -> None:
    """No-op since migration 0147 — see ``apply_released_filter``. A
    moderated tenant may open the scheda of any contatto it owns, so
    single-lead reads/actions must never 404 on the moderation gate.
    """
    return None
