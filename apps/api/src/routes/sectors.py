"""Sector palette catalogue — read-only, public.

Surfaces:

  GET /v1/sectors/wizard-groups
      Returns the palette of wizard_groups available in
      ``ateco_google_types``. Used by the onboarding wizard
      "Settori target" multi-select to render labelled checkboxes
      with example ATECO codes and typical kWp ranges.

The endpoint is unauthenticated reference data, mirroring the
pattern of ``ateco_google_types`` (read-all RLS policy from migration
0014). No tenant scoping required.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

router = APIRouter()
log = get_logger(__name__)


class WizardGroup(BaseModel):
    """One sector palette as exposed to the onboarding UI.

    The `display_name` falls back to the wizard_group code humanised
    when no per-group label is curated. ATECO examples are the top-3
    `ateco_label` strings from the seed (alphabetical by code).
    """

    wizard_group: str
    display_name: str
    description: str | None = None
    ateco_examples: list[str] = Field(default_factory=list)
    typical_kwp_range_min: int | None = None
    typical_kwp_range_max: int | None = None


# Curated display names for wizard_groups. When a group isn't listed
# here, we fall back to humanising the snake_case code.
_DISPLAY_NAMES: dict[str, tuple[str, str]] = {
    "industry_heavy": (
        "Manifatturiero pesante",
        "Metalmeccanico, lavorazione metalli, fonderie, carpenteria, chimica industriale.",
    ),
    "industry_light": (
        "Manifatturiero leggero",
        "Tessile, abbigliamento, plastica, carta, stampa, lavorazioni leggere.",
    ),
    "food_production": (
        "Produzione alimentare",
        "Industria alimentare, bevande, lavorazione carne, caseifici, mangimifici.",
    ),
    "logistics": (
        "Logistica e magazzinaggio",
        "Centri logistici, magazzini, hub spedizioni, distribuzione.",
    ),
    "retail_gdo": (
        "Grande distribuzione",
        "Ipermercati, centri commerciali, cash and carry, grossisti.",
    ),
    "horeca": (
        "Ristorazione e bar",
        "Ristoranti retail, bar, caffetterie, pizzerie.",
    ),
    "hospitality_large": (
        "Ricettivo grande",
        "Hotel 4-5 stelle, resort, hotel congressuali e business.",
    ),
    "hospitality_food_service": (
        "Ristorazione collettiva",
        "Catering industriale, mense aziendali, ristorazione ospedaliera.",
    ),
    "healthcare": (
        "Sanitario",
        "Ospedali, cliniche generaliste, poliambulatori.",
    ),
    "healthcare_private": (
        "Sanitario privato",
        "Case di cura private, RSA, centri diagnostici.",
    ),
    "agricultural_intensive": (
        "Agricolo intensivo",
        "Allevamenti intensivi (bovini, suini, avicolo), agroindustria, aziende agricole strutturate.",
    ),
    "automotive": (
        "Automotive",
        "Concessionarie, autosaloni, officine grandi, carrozzerie.",
    ),
    "education": (
        "Istruzione",
        "Scuole superiori, università, istituti tecnici.",
    ),
    "personal_services": (
        "Servizi alla persona",
        "Palestre, spa, centri benessere, piscine coperte.",
    ),
    "professional_offices": (
        "Uffici professionali",
        "Studi legali, notarili, agenzie immobiliari, banche, assicurazioni.",
    ),
}


def _humanise_wizard_group(code: str) -> str:
    """Fallback when curated display_name is missing — turn snake_case
    into Title Case: ``industry_heavy`` → ``Industry Heavy``."""
    return " ".join(part.capitalize() for part in code.split("_") if part)


@router.get("/wizard-groups", response_model=list[WizardGroup])
async def list_wizard_groups() -> list[WizardGroup]:
    """Return one entry per distinct ``wizard_group`` in
    ``ateco_google_types``, with display metadata and a few sample
    ATECO labels for the UI tooltip.
    """
    sb = get_service_client()
    res = (
        sb.table("ateco_google_types")
        .select(
            "ateco_code, ateco_label, wizard_group, "
            "typical_kwp_range_min, typical_kwp_range_max"
        )
        .order("wizard_group")
        .order("priority_hint", desc=True)
        .execute()
    )
    rows: list[dict[str, Any]] = res.data or []

    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        wg = r.get("wizard_group")
        if not wg:
            continue
        grouped.setdefault(wg, []).append(r)

    out: list[WizardGroup] = []
    for wg, entries in grouped.items():
        display, desc = _DISPLAY_NAMES.get(wg, (_humanise_wizard_group(wg), None))
        # ATECO examples — top 3 by priority_hint already (we ordered
        # in the SELECT). Fall back to sorting alphabetically when
        # priority_hint is uniformly null.
        examples = [
            e.get("ateco_label") or e.get("ateco_code") or ""
            for e in entries[:3]
            if e.get("ateco_label") or e.get("ateco_code")
        ]
        # KWP range: smallest min and largest max across the group's
        # rows (broadest envelope).
        kwp_min: int | None = None
        kwp_max: int | None = None
        for e in entries:
            mn = e.get("typical_kwp_range_min")
            mx = e.get("typical_kwp_range_max")
            if mn is not None:
                mn_int = int(mn)
                kwp_min = mn_int if kwp_min is None else min(kwp_min, mn_int)
            if mx is not None:
                mx_int = int(mx)
                kwp_max = mx_int if kwp_max is None else max(kwp_max, mx_int)

        out.append(
            WizardGroup(
                wizard_group=wg,
                display_name=display,
                description=desc,
                ateco_examples=examples,
                typical_kwp_range_min=kwp_min,
                typical_kwp_range_max=kwp_max,
            )
        )

    # Stable sort: curated groups first (in DISPLAY_NAMES order), then
    # any uncurated tail alphabetically. The wizard renders in this
    # exact order so the UX is deterministic.
    curated_order = list(_DISPLAY_NAMES.keys())
    out.sort(
        key=lambda g: (
            curated_order.index(g.wizard_group)
            if g.wizard_group in curated_order
            else len(curated_order),
            g.wizard_group,
        )
    )
    return out
