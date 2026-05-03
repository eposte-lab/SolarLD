"""Lead → Preventivo conversion service.

This is the bridge between a hot lead (in `leads` + `subjects` + roof
analysis + roi_data) and a formal, editable quote document. It does
three things:

  1. ``build_auto_fields`` — gather every AUTO field the preventivo
     template needs, from the lead row + the joined subject + tenant
     branding + a fresh ROI estimate. Returns a dict shaped exactly
     to the template variable names (``tenant_*``, ``azienda_*``,
     ``solar_*``, ``econ_*``, ``render_after_url``). The installer
     never edits these — they're a snapshot of "what the system
     knows" at issue time.

  2. ``next_preventivo_number`` — atomically allocate the next per-
     tenant sequence number via the ``next_quote_seq`` RPC defined
     in migration 0081. Returns the formatted string (``2026/PV/0042``)
     and the raw int.

  3. ``save_quote`` — assemble auto + manual fields, render the PDF
     via WeasyPrint (in a thread executor — WeasyPrint is sync-only
     and CPU-heavy, ~500 ms-2 s per render), upload to the renderings
     bucket, mark prior issued versions as ``superseded``, INSERT the
     new row.

Pure-ish: the rendering is offloaded but every other op is a single
Supabase round-trip so test stubs are straightforward.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from .quote_pdf_renderer import render_quote_pdf
from .roi_service import compute_roi
from .storage_service import upload_bytes

log = get_logger(__name__)

# Same bucket the creative agent uses for before/after PNGs and GIFs.
# Keeps every per-lead asset in one place: `renderings/{tenant}/{lead}/...`.
RENDERINGS_BUCKET = "renderings"


# ---------------------------------------------------------------------------
# Public dataclass — the row shape the API returns to the dashboard.
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class LeadQuote:
    id: str
    tenant_id: str
    lead_id: str
    preventivo_number: str
    preventivo_seq: int
    version: int
    status: str
    pdf_url: str | None
    hero_url: str | None
    auto_fields: dict[str, Any]
    manual_fields: dict[str, Any]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "LeadQuote":
        return cls(
            id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            lead_id=str(row["lead_id"]),
            preventivo_number=row["preventivo_number"],
            preventivo_seq=int(row["preventivo_seq"]),
            version=int(row["version"]),
            status=row["status"],
            pdf_url=row.get("pdf_url"),
            hero_url=row.get("hero_url"),
            auto_fields=row.get("auto_fields") or {},
            manual_fields=row.get("manual_fields") or {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# AUTO fields builder
# ---------------------------------------------------------------------------


def build_auto_fields(lead_id: str | UUID, tenant_id: str | UUID) -> dict[str, Any]:
    """Pre-populate the AUTO bag for the preventivo template.

    Keys map 1:1 to the placeholders in ``preventivo.html.j2`` (which
    is itself a port of the user-supplied HTML template). Manual fields
    are merged on top of this dict by ``save_quote``.

    Raises ``ValueError`` if the lead doesn't belong to ``tenant_id``
    or doesn't exist — the route layer turns that into 404.
    """
    sb = get_service_client()

    # 1. Lead with joined subject — single round-trip via Supabase
    #    PostgREST embedded resource syntax.
    lead_res = (
        sb.table("leads")
        .select("*, subjects(*), roofs(*)")
        .eq("id", str(lead_id))
        .eq("tenant_id", str(tenant_id))
        .limit(1)
        .execute()
    )
    if not lead_res.data:
        raise ValueError(f"lead {lead_id} not found for tenant {tenant_id}")
    lead = lead_res.data[0]
    subject = lead.get("subjects") or {}
    roof = lead.get("roofs") or {}

    # 2. Tenant branding + business info + cost_assumptions for ROI
    #    overrides (Sprint 1.3 — replace the hardcoded 0,22 €/kWh with
    #    the per-tenant grid tariff when configured).
    tenant_res = (
        sb.table("tenants")
        .select(
            "id, business_name, legal_name, vat_number, contact_email, "
            "contact_phone, brand_logo_url, brand_primary_color, settings, "
            "cost_assumptions"
        )
        .eq("id", str(tenant_id))
        .limit(1)
        .execute()
    )
    tenant = tenant_res.data[0] if tenant_res.data else {}
    settings = tenant.get("settings") or {}

    # 3. ROI source — single source of truth (Sprint 1.1).
    #    Prefer the persisted ``roof.derivations`` (computed by
    #    ``compute_full_derivations`` at roof-write time, refreshed
    #    when the customer uploads a bolletta in
    #    ``routes/public.upload_bolletta``) so the preventivo PDF
    #    shows the same numbers as the dashboard inspector, the
    #    email body, and the lead-portal page. Recompute only as
    #    fallback for legacy roofs (pre-migration 0094) where
    #    ``derivations`` is null.
    persisted = roof.get("derivations") or {}
    if persisted:
        roi_jsonb = dict(persisted)
    else:
        roi = compute_roi(
            estimated_kwp=lead.get("estimated_kwp") or roof.get("estimated_kwp"),
            estimated_yearly_kwh=lead.get("estimated_yearly_kwh")
            or roof.get("estimated_yearly_kwh"),
            subject_type=(subject.get("type") or "b2b").lower(),
            roi_target_years=settings.get("roi_target_years"),
        )
        roi_jsonb = roi.to_jsonb() if roi else {}

    # 4. Hero image fallback chain (see plan: after.png is NOT always
    #    present — three failure paths in creative.py leave it null).
    #    Fallback order: rendering_image_url → before.png path →
    #    tenant brand logo → None (template has a hero-blank state).
    hero_url = lead.get("rendering_image_url")
    if not hero_url:
        # Synthesize the before.png URL from the storage convention.
        # Cheap to attempt — if the file doesn't exist, the template
        # still renders (the <img> just shows a broken icon, which the
        # CSS fallback hides via background color).
        try:
            sb_client = get_service_client()
            before_path = f"{tenant_id}/{lead_id}/before.png"
            hero_url = sb_client.storage.from_(RENDERINGS_BUCKET).get_public_url(
                before_path
            )
        except Exception:  # noqa: BLE001 — best-effort fallback
            hero_url = None
    if not hero_url:
        hero_url = tenant.get("brand_logo_url")

    # 5. Solar / production figures the template needs explicitly.
    #    Template var names follow the user's spec; fall back to roi
    #    when the lead row doesn't carry the typed column.
    kwp = (
        lead.get("estimated_kwp")
        or roof.get("estimated_kwp")
        or roi_jsonb.get("estimated_kwp")
        or 0
    )
    yearly_kwh = (
        lead.get("estimated_yearly_kwh")
        or roof.get("estimated_yearly_kwh")
        or roi_jsonb.get("yearly_kwh")
        or 0
    )

    # 6. Build the bag. Keep every key present (use empty string
    #    rather than None) — the template renders friendlier when
    #    a placeholder shows blank vs. literal "None".
    today_iso = datetime.now(timezone.utc).date().isoformat()

    auto: dict[str, Any] = {
        # Tenant / installer block ----------------------------------------
        "tenant_company_name": tenant.get("business_name") or "",
        "tenant_logo_url": tenant.get("brand_logo_url") or "",
        "tenant_brand_color": tenant.get("brand_primary_color") or "#0F766E",
        "tenant_brand_color_accent": settings.get("brand_color_accent")
        or tenant.get("brand_primary_color")
        or "#F4A300",
        "tenant_email": tenant.get("contact_email") or "",
        "tenant_telefono": tenant.get("contact_phone") or "",
        "tenant_pec": settings.get("pec") or "",
        "tenant_piva": tenant.get("vat_number") or "",
        "tenant_sede_legale": settings.get("sede_legale") or "",
        "tenant_sede_operativa": settings.get("sede_operativa")
        or settings.get("sede_legale")
        or "",
        "tenant_iscrizione_albo": settings.get("iscrizione_albo") or "",
        "tenant_anni_esperienza": settings.get("anni_esperienza") or "",
        "tenant_impianti_installati": settings.get("impianti_installati") or "",
        # Cliente / azienda block -----------------------------------------
        "azienda_ragione_sociale": subject.get("business_name")
        or subject.get("legal_name")
        or "",
        "azienda_piva": subject.get("vat_number") or "",
        "azienda_sede_legale": _format_address(
            subject.get("hq_address"),
            subject.get("hq_cap"),
            subject.get("hq_city"),
            subject.get("hq_province"),
        ),
        "azienda_sede_operativa": _format_address(
            subject.get("sede_operativa_address"),
            subject.get("sede_operativa_cap"),
            subject.get("sede_operativa_city"),
            subject.get("sede_operativa_province"),
        )
        or _format_address(
            subject.get("hq_address"),
            subject.get("hq_cap"),
            subject.get("hq_city"),
            subject.get("hq_province"),
        ),
        "azienda_settore": subject.get("ateco_description")
        or subject.get("ateco_code")
        or "",
        "azienda_decisore_nome": _full_name(
            subject.get("owner_first_name"), subject.get("owner_last_name")
        ),
        "azienda_decisore_ruolo": subject.get("owner_role") or "",
        # Solar configuration block ---------------------------------------
        "solar_m2_tetto": _to_int(roof.get("usable_area_m2")),
        "solar_kw_installabili": _to_round(kwp, 1),
        "solar_kwh_annui": _to_int(yearly_kwh),
        "solar_pannelli_numero": _to_int(roof.get("estimated_panel_count")),
        "solar_orientamento": roof.get("primary_orientation") or "",
        "solar_inclinazione": _to_int(roof.get("primary_tilt_deg")),
        "solar_irraggiamento_kwh_m2": _to_int(roof.get("ghi_kwh_m2_year")),
        "solar_imagery_quality": roof.get("imagery_quality") or "",
        # Economic block (4 of the 7 metrics + extras for the cashflow).
        #
        # Sprint 1.3 — grid_price + self-consumption pulled from the
        # tenant's cost_assumptions (or the persisted derivations'
        # assumptions_resolved, which already encoded the per-tenant
        # values when the roof was written). Falls back to the
        # ``roi_service`` module defaults only when the tenant
        # hasn't configured anything. No more hardcoded 0,22.
        # The manual_fields merge in save_quote can still override
        # for the rare case where the customer's actual tariff
        # differs from the tenant default.
        "econ_consumo_stimato_kwh": _to_int(yearly_kwh),
        "econ_costo_kwh_attuale": _resolve_grid_price_eur_per_kwh(
            tenant=tenant,
            subject_type=(subject.get("type") or "b2b").lower(),
            assumptions_resolved=persisted.get("assumptions_resolved")
            if isinstance(persisted, dict)
            else None,
        ),
        "econ_costo_attuale_anno": _to_int(
            yearly_kwh
            * _resolve_grid_price_float(
                tenant=tenant,
                subject_type=(subject.get("type") or "b2b").lower(),
                assumptions_resolved=persisted.get("assumptions_resolved")
                if isinstance(persisted, dict)
                else None,
            )
        ),
        "econ_copertura_perc": _to_int(
            _resolve_self_consumption_pct(
                tenant=tenant,
                subject_type=(subject.get("type") or "b2b").lower(),
                assumptions_resolved=persisted.get("assumptions_resolved")
                if isinstance(persisted, dict)
                else None,
            )
        ),
        "econ_risparmio_anno_1": _to_int(roi_jsonb.get("net_self_savings_eur")),
        "econ_risparmio_25_anni": _to_int(roi_jsonb.get("savings_25y_eur")),
        "econ_payback_anni": roi_jsonb.get("payback_years") or 0,
        "econ_irr_25_anni": _to_int(roi_jsonb.get("roi_pct_25y")),
        "econ_co2_ton_anno": _to_round(
            (roi_jsonb.get("co2_kg_per_year") or 0) / 1000.0, 1
        ),
        "econ_co2_25_anni": _to_round(
            roi_jsonb.get("co2_tonnes_25_years") or 0, 1
        ),
        "econ_alberi_equivalenti": int(roi_jsonb.get("trees_equivalent") or 0),
        # Render hero -----------------------------------------------------
        "render_after_url": hero_url or "",
        # Cashflow series (25 years). Generated once here so the editor
        # can show a preview and the renderer doesn't have to recompute.
        "cashflow_years": _build_cashflow_years(
            yearly_savings=roi_jsonb.get("net_self_savings_eur") or 0,
            net_capex=roi_jsonb.get("net_capex_eur") or 0,
            yearly_kwh=yearly_kwh,
        ),
        # Convenience defaults ---------------------------------------------
        "preventivo_data": today_iso,
    }
    return auto


# ---------------------------------------------------------------------------
# Cost assumption resolvers (Sprint 1.3)
#
# Three sources for grid price / self-consumption %, in priority order:
#   1. ``persisted.assumptions_resolved`` — what compute_full_derivations
#      actually used at roof-write time. Most accurate because it
#      includes per-tenant overrides AND any consumption_source
#      metadata from a bolletta upload.
#   2. ``tenant.cost_assumptions`` — current tenant override (may
#      have changed since the roof was written; we read the live
#      value as fallback so quotes generated months later reflect
#      newer tenant config).
#   3. ``roi_service`` module defaults — public Italian PV market
#      averages.
# ---------------------------------------------------------------------------


def _resolve_grid_price_float(
    *,
    tenant: dict[str, Any],
    subject_type: str,
    assumptions_resolved: dict[str, Any] | None,
) -> float:
    """Live €/kWh grid price for this tenant + subject classification."""
    if assumptions_resolved and "grid_price" in assumptions_resolved:
        try:
            return float(assumptions_resolved["grid_price"])
        except (TypeError, ValueError):
            pass

    overrides = tenant.get("cost_assumptions") or {}
    key = (
        "grid_price_eur_per_kwh_b2b"
        if subject_type == "b2b"
        else "grid_price_eur_per_kwh_b2c"
    )
    if key in overrides:
        try:
            return float(overrides[key])
        except (TypeError, ValueError):
            pass

    # Fall back to the canonical Italian-market defaults from
    # roi_service. Lazy import to avoid circular module deps.
    from . import roi_service

    return (
        roi_service.GRID_PRICE_EUR_PER_KWH_B2B
        if subject_type == "b2b"
        else roi_service.GRID_PRICE_EUR_PER_KWH_B2C
    )


def _resolve_grid_price_eur_per_kwh(
    *,
    tenant: dict[str, Any],
    subject_type: str,
    assumptions_resolved: dict[str, Any] | None,
) -> str:
    """Italian-locale formatted €/kWh string for the PDF template (e.g. ``0,27``)."""
    val = _resolve_grid_price_float(
        tenant=tenant,
        subject_type=subject_type,
        assumptions_resolved=assumptions_resolved,
    )
    return f"{val:.2f}".replace(".", ",")


def _resolve_self_consumption_pct(
    *,
    tenant: dict[str, Any],
    subject_type: str,
    assumptions_resolved: dict[str, Any] | None,
) -> float:
    """Self-consumption percentage 0..100 for the preventivo header.

    Same priority chain as grid price — derivations snapshot >
    tenant override > module default. Returned as a percentage
    (e.g. 65.0) because the template renders it as ``65 %``.
    """
    if assumptions_resolved and "self_ratio" in assumptions_resolved:
        try:
            return float(assumptions_resolved["self_ratio"]) * 100.0
        except (TypeError, ValueError):
            pass

    overrides = tenant.get("cost_assumptions") or {}
    key = (
        "self_consumption_ratio_b2b"
        if subject_type == "b2b"
        else "self_consumption_ratio_b2c"
    )
    if key in overrides:
        try:
            return float(overrides[key]) * 100.0
        except (TypeError, ValueError):
            pass

    from . import roi_service

    return (
        roi_service.SELF_CONSUMPTION_RATIO_B2B * 100.0
        if subject_type == "b2b"
        else roi_service.SELF_CONSUMPTION_RATIO_B2C * 100.0
    )


# ---------------------------------------------------------------------------
# Numbering — RPC backed
# ---------------------------------------------------------------------------


def next_preventivo_number(tenant_id: str | UUID) -> tuple[str, int]:
    """Atomically allocate ``YYYY/PV/NNNN`` and the raw seq.

    Backed by ``next_quote_seq(uuid)`` RPC defined in migration 0081.
    The RPC uses ``INSERT … ON CONFLICT DO UPDATE`` on the
    per-tenant counter row, which is row-level atomic in Postgres
    (no race even with concurrent saves).
    """
    sb = get_service_client()
    res = sb.rpc("next_quote_seq", {"p_tenant_id": str(tenant_id)}).execute()
    seq = int(res.data) if isinstance(res.data, int) else int(res.data or 0)
    if seq <= 0:
        # Defensive: if the RPC returned 0/null we'd silently mint
        # duplicate numbers. Hard-fail instead so the route surfaces it.
        raise RuntimeError(f"next_quote_seq returned non-positive seq: {res.data!r}")
    year = datetime.now(timezone.utc).year
    return f"{year}/PV/{seq:04d}", seq


# ---------------------------------------------------------------------------
# Save & render
# ---------------------------------------------------------------------------


async def save_quote(
    *,
    lead_id: str | UUID,
    tenant_id: str | UUID,
    manual_fields: dict[str, Any],
) -> LeadQuote:
    """End-to-end save: snapshot AUTO, allocate number, render PDF,
    upload, supersede previous versions, INSERT the new row.

    WeasyPrint is synchronous and CPU-heavy (~500 ms-2 s, ~80 MB peak
    per render). Calling it directly from an async handler stalls the
    event loop. ``asyncio.to_thread`` offloads it.
    """
    sb = get_service_client()
    auto = build_auto_fields(lead_id, tenant_id)
    number, seq = next_preventivo_number(tenant_id)

    # Determine next version. We could compute this server-side via SQL
    # MAX, but the read-then-insert is fine here because the (tenant,
    # preventivo_number) UNIQUE constraint already guarantees that no
    # two saves end up with the same identifier — and the seq via RPC
    # is monotonic.
    prev_res = (
        sb.table("lead_quotes")
        .select("version")
        .eq("lead_id", str(lead_id))
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    prev_version = (prev_res.data[0]["version"] if prev_res.data else 0) or 0
    next_version = prev_version + 1

    # Mark previously-issued versions as superseded so the UI's
    # "current vs. history" split is clean.
    sb.table("lead_quotes").update({"status": "superseded"}).eq(
        "lead_id", str(lead_id)
    ).eq("status", "issued").execute()

    # Render PDF off the event loop. Errors propagate up to the route,
    # which translates them into a 500 with a clear log line.
    ctx = {
        **auto,
        **(manual_fields or {}),
        "preventivo_numero": (manual_fields or {}).get("preventivo_numero") or number,
    }
    pdf_bytes = await asyncio.to_thread(render_quote_pdf, ctx)

    # Upload — same bucket + path convention the creative agent uses
    # (`renderings/{tenant_id}/{lead_id}/...`). Suffixing with the seq
    # keeps every version addressable; we never overwrite v1 when v2
    # is saved.
    pdf_path = f"{tenant_id}/{lead_id}/quote-{seq:04d}.pdf"
    pdf_url = upload_bytes(
        RENDERINGS_BUCKET,
        pdf_path,
        pdf_bytes,
        content_type="application/pdf",
        upsert=True,
    )

    # Insert the new row. We use the resolved hero_url from auto_fields
    # so a re-render in the future is reproducible from this row alone.
    insert_res = (
        sb.table("lead_quotes")
        .insert(
            {
                "tenant_id": str(tenant_id),
                "lead_id": str(lead_id),
                "preventivo_number": ctx["preventivo_numero"],
                "preventivo_seq": seq,
                "version": next_version,
                "status": "issued",
                "auto_fields": auto,
                "manual_fields": manual_fields or {},
                "pdf_url": pdf_url,
                "hero_url": auto.get("render_after_url") or None,
            }
        )
        .execute()
    )
    if not insert_res.data:
        raise RuntimeError("lead_quotes insert returned no rows")
    log.info(
        "quote.saved",
        tenant_id=str(tenant_id),
        lead_id=str(lead_id),
        preventivo_number=ctx["preventivo_numero"],
        version=next_version,
        pdf_size=len(pdf_bytes),
    )
    return LeadQuote.from_row(insert_res.data[0])


def list_quotes_for_lead(lead_id: str | UUID, tenant_id: str | UUID) -> list[LeadQuote]:
    """Return all versions of every preventivo for a lead, newest first."""
    sb = get_service_client()
    res = (
        sb.table("lead_quotes")
        .select("*")
        .eq("lead_id", str(lead_id))
        .eq("tenant_id", str(tenant_id))
        .order("version", desc=True)
        .execute()
    )
    return [LeadQuote.from_row(r) for r in (res.data or [])]


def get_quote(quote_id: str | UUID, tenant_id: str | UUID) -> LeadQuote | None:
    """Fetch a single quote by id, scoped to tenant. Returns None if missing."""
    sb = get_service_client()
    res = (
        sb.table("lead_quotes")
        .select("*")
        .eq("id", str(quote_id))
        .eq("tenant_id", str(tenant_id))
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    return LeadQuote.from_row(res.data[0])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_address(
    street: str | None,
    cap: str | None,
    city: str | None,
    province: str | None,
) -> str:
    parts: list[str] = []
    if street:
        parts.append(street)
    locality = " ".join(p for p in [cap, city] if p)
    if locality:
        parts.append(locality)
    if province:
        parts.append(f"({province})")
    return ", ".join(parts).strip(", ")


def _full_name(first: str | None, last: str | None) -> str:
    return " ".join(p for p in [first, last] if p).strip()


def _to_int(val: object) -> int:
    try:
        return int(round(float(val)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _to_round(val: object, ndigits: int) -> float:
    try:
        return round(float(val), ndigits)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _build_cashflow_years(
    *,
    yearly_savings: float,
    net_capex: float,
    yearly_kwh: float,
) -> list[dict[str, Any]]:
    """25-year cashflow series for the preventivo Cashflow page.

    Conservative: 0.5%/yr panel degradation, fixed maintenance €150/yr
    after year 5 (warranty period), payback flagged when cumulative
    crosses zero. Returns a list of dicts the template iterates over
    via ``{% for y in cashflow_years %}``.
    """
    rows: list[dict[str, Any]] = []
    cumulative = -float(net_capex or 0.0)
    payback_year_set = False
    for i in range(1, 26):
        # Year-over-year degradation: production drops ~0.5% per year.
        degradation = (1.0 - 0.005) ** (i - 1)
        kwh = yearly_kwh * degradation
        savings = (yearly_savings or 0.0) * degradation
        # Inverter replacement assumption around year 12 (~€1,500 B2B).
        maintenance = 150.0 if i >= 6 else 0.0
        if i == 12:
            maintenance += 1500.0
        net = savings - maintenance
        cumulative += net
        is_payback = (not payback_year_set) and cumulative >= 0
        if is_payback:
            payback_year_set = True
        rows.append(
            {
                "year_number": i,
                "kwh_produced": int(round(kwh)),
                "savings_eur": int(round(savings)),
                "maintenance_cost": int(round(maintenance)),
                "net_cashflow": int(round(net)),
                "cumulative_cashflow": int(round(cumulative)),
                "is_payback_year": is_payback,
            }
        )
    return rows
