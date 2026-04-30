"""Unit tests for ``quote_service.py``.

The service is a thin orchestrator around Supabase calls + the PDF
renderer. We mock ``get_service_client`` and ``render_quote_pdf`` so
each test exercises one branch of the orchestration without spinning
up Postgres or WeasyPrint.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_query(data: list[dict[str, Any]] | dict[str, Any] | None = None) -> MagicMock:
    """Build a chainable MagicMock that mirrors the supabase-py builder
    pattern (``.table(..).select(..).eq(..).execute()`` returns
    ``Result(data=...)``)."""
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=data)
    # Every chained method returns self so any combination resolves.
    for method in (
        "select",
        "insert",
        "update",
        "eq",
        "limit",
        "order",
        "in_",
        "neq",
    ):
        getattr(chain, method).return_value = chain
    return chain


def _stub_supabase(
    *,
    lead_row: dict[str, Any] | None = None,
    tenant_row: dict[str, Any] | None = None,
    rpc_seq: int = 1,
    prev_versions: list[dict[str, Any]] | None = None,
    insert_returning: list[dict[str, Any]] | None = None,
) -> MagicMock:
    sb = MagicMock()

    # Each .table(name) call returns a fresh chain so per-table
    # data is realistic.
    def table(name: str) -> MagicMock:
        if name == "leads":
            return _stub_query(data=[lead_row] if lead_row else [])
        if name == "tenants":
            return _stub_query(data=[tenant_row] if tenant_row else [])
        if name == "lead_quotes":
            # The service queries it twice in save_quote: once for prev
            # version (select), once for update-supersede, once for
            # insert. A single chain returning the right .data on each
            # execute() is fine because each call to .table('lead_quotes')
            # makes a NEW chain via this factory.
            chain = MagicMock()
            chain.select.return_value = chain
            chain.eq.return_value = chain
            chain.order.return_value = chain
            chain.limit.return_value = chain
            chain.update.return_value = chain
            chain.insert.return_value = chain
            chain.execute.side_effect = None
            chain.execute.return_value = MagicMock(
                data=insert_returning or prev_versions or []
            )
            return chain
        return _stub_query()

    sb.table.side_effect = table

    # RPC for next_quote_seq
    rpc_chain = MagicMock()
    rpc_chain.execute.return_value = MagicMock(data=rpc_seq)
    sb.rpc.return_value = rpc_chain

    # Storage helper for hero fallback URL
    storage_chain = MagicMock()
    storage_chain.get_public_url.return_value = "https://example.invalid/before.png"
    sb.storage.from_.return_value = storage_chain

    return sb


# ---------------------------------------------------------------------------
# build_auto_fields
# ---------------------------------------------------------------------------


def _typical_lead_row() -> dict[str, Any]:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "estimated_kwp": 50.0,
        "estimated_yearly_kwh": 65000.0,
        "rendering_image_url": "https://example.invalid/after.png",
        "subjects": {
            "type": "b2b",
            "business_name": "Officine Liguria SRL",
            "vat_number": "01234567890",
            "hq_address": "Via Roma 1",
            "hq_cap": "16100",
            "hq_city": "Genova",
            "hq_province": "GE",
            "owner_first_name": "Marco",
            "owner_last_name": "Rossi",
            "owner_role": "Amministratore",
            "ateco_description": "Officine meccaniche di precisione",
        },
        "roofs": {
            "estimated_kwp": 50.0,
            "estimated_yearly_kwh": 65000.0,
            "estimated_panel_count": 86,
            "primary_orientation": "Sud",
            "primary_tilt_deg": 15,
            "ghi_kwh_m2_year": 1450,
            "imagery_quality": "HIGH",
            "usable_area_m2": 320,
        },
    }


def _typical_tenant_row() -> dict[str, Any]:
    return {
        "id": "22222222-2222-2222-2222-222222222222",
        "business_name": "SolarLED Installazioni",
        "vat_number": "98765432101",
        "contact_email": "info@solarled.it",
        "contact_phone": "+39 010 1234567",
        "brand_logo_url": "https://example.invalid/logo.png",
        "brand_primary_color": "#0F766E",
        "settings": {
            "roi_target_years": 7,
            "anni_esperienza": 12,
            "impianti_installati": 240,
        },
    }


def test_build_auto_fields_populates_all_template_keys() -> None:
    from src.services import quote_service

    sb = _stub_supabase(
        lead_row=_typical_lead_row(),
        tenant_row=_typical_tenant_row(),
    )
    with patch.object(quote_service, "get_service_client", return_value=sb):
        auto = quote_service.build_auto_fields(
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        )

    # Tenant block
    assert auto["tenant_company_name"] == "SolarLED Installazioni"
    assert auto["tenant_brand_color"] == "#0F766E"
    assert auto["tenant_anni_esperienza"] == 12

    # Cliente / azienda block
    assert auto["azienda_ragione_sociale"] == "Officine Liguria SRL"
    assert auto["azienda_decisore_nome"] == "Marco Rossi"
    assert "Via Roma 1" in auto["azienda_sede_legale"]
    assert "Genova" in auto["azienda_sede_legale"]

    # Solar block — kWp uses lead value
    assert auto["solar_kw_installabili"] == 50.0
    assert auto["solar_kwh_annui"] == 65000
    assert auto["solar_pannelli_numero"] == 86

    # Econ block — fresh ROI must populate the 7 metrics
    assert auto["econ_risparmio_anno_1"] > 0
    assert auto["econ_risparmio_25_anni"] > 0
    assert auto["econ_alberi_equivalenti"] > 0

    # Hero passthrough
    assert auto["render_after_url"] == "https://example.invalid/after.png"

    # Cashflow series exists with 25 rows and at least one payback flag
    cf = auto["cashflow_years"]
    assert len(cf) == 25
    assert cf[0]["year_number"] == 1
    assert any(row["is_payback_year"] for row in cf)


def test_build_auto_fields_falls_back_to_before_png_when_render_missing() -> None:
    """A lead with no AI render should still produce a hero URL via the
    storage convention fallback (renderings/{tenant}/{lead}/before.png)."""
    from src.services import quote_service

    lead = _typical_lead_row()
    lead["rendering_image_url"] = None

    sb = _stub_supabase(lead_row=lead, tenant_row=_typical_tenant_row())
    with patch.object(quote_service, "get_service_client", return_value=sb):
        auto = quote_service.build_auto_fields(
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        )

    # Falls back to the synthesized before.png URL from the storage stub.
    assert auto["render_after_url"] == "https://example.invalid/before.png"


def test_build_auto_fields_404_when_lead_missing() -> None:
    from src.services import quote_service

    sb = _stub_supabase(lead_row=None, tenant_row=_typical_tenant_row())
    with patch.object(quote_service, "get_service_client", return_value=sb):
        with pytest.raises(ValueError):
            quote_service.build_auto_fields(
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            )


# ---------------------------------------------------------------------------
# next_preventivo_number
# ---------------------------------------------------------------------------


def test_next_preventivo_number_formats_year_and_seq() -> None:
    from datetime import datetime, timezone

    from src.services import quote_service

    sb = _stub_supabase(rpc_seq=42)
    with patch.object(quote_service, "get_service_client", return_value=sb):
        number, seq = quote_service.next_preventivo_number(
            "22222222-2222-2222-2222-222222222222"
        )

    expected_year = datetime.now(timezone.utc).year
    assert number == f"{expected_year}/PV/0042"
    assert seq == 42


def test_next_preventivo_number_rejects_zero_seq() -> None:
    """If the RPC silently returned 0/null we'd mint duplicates. Hard fail."""
    from src.services import quote_service

    sb = _stub_supabase(rpc_seq=0)
    with patch.object(quote_service, "get_service_client", return_value=sb):
        with pytest.raises(RuntimeError):
            quote_service.next_preventivo_number(
                "22222222-2222-2222-2222-222222222222"
            )


# ---------------------------------------------------------------------------
# save_quote — version bump + supersede behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_quote_increments_version_and_inserts() -> None:
    from src.services import quote_service

    inserted_row = {
        "id": "33333333-3333-3333-3333-333333333333",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "lead_id": "11111111-1111-1111-1111-111111111111",
        "preventivo_number": "2026/PV/0001",
        "preventivo_seq": 1,
        "version": 2,
        "status": "issued",
        "auto_fields": {},
        "manual_fields": {"prezzo_finale": 25000},
        "pdf_url": "https://example.invalid/quote.pdf",
        "hero_url": "https://example.invalid/after.png",
        "created_at": "2026-04-29T00:00:00Z",
        "updated_at": "2026-04-29T00:00:00Z",
    }
    sb = _stub_supabase(
        lead_row=_typical_lead_row(),
        tenant_row=_typical_tenant_row(),
        rpc_seq=1,
        # The select-for-prev-version call returns version=1 → next is 2
        prev_versions=[{"version": 1}],
        insert_returning=[inserted_row],
    )

    with (
        patch.object(quote_service, "get_service_client", return_value=sb),
        patch.object(
            quote_service, "render_quote_pdf", return_value=b"%PDF-fake"
        ),
        patch.object(
            quote_service,
            "upload_bytes",
            return_value="https://example.invalid/quote.pdf",
        ),
    ):
        quote = await quote_service.save_quote(
            lead_id="11111111-1111-1111-1111-111111111111",
            tenant_id="22222222-2222-2222-2222-222222222222",
            manual_fields={"prezzo_finale": 25000},
        )

    assert quote.version == 2
    assert quote.status == "issued"
    assert quote.preventivo_number == "2026/PV/0001"


@pytest.mark.asyncio
async def test_save_quote_starts_at_version_1_when_no_prior() -> None:
    from src.services import quote_service

    inserted_row = {
        "id": "44444444-4444-4444-4444-444444444444",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "lead_id": "11111111-1111-1111-1111-111111111111",
        "preventivo_number": "2026/PV/0001",
        "preventivo_seq": 1,
        "version": 1,
        "status": "issued",
        "auto_fields": {},
        "manual_fields": {},
        "pdf_url": "https://example.invalid/quote.pdf",
        "hero_url": None,
        "created_at": "2026-04-29T00:00:00Z",
        "updated_at": "2026-04-29T00:00:00Z",
    }
    sb = _stub_supabase(
        lead_row=_typical_lead_row(),
        tenant_row=_typical_tenant_row(),
        rpc_seq=1,
        prev_versions=[],  # no prior — version starts at 1
        insert_returning=[inserted_row],
    )

    with (
        patch.object(quote_service, "get_service_client", return_value=sb),
        patch.object(
            quote_service, "render_quote_pdf", return_value=b"%PDF-fake"
        ),
        patch.object(
            quote_service,
            "upload_bytes",
            return_value="https://example.invalid/quote.pdf",
        ),
    ):
        quote = await quote_service.save_quote(
            lead_id="11111111-1111-1111-1111-111111111111",
            tenant_id="22222222-2222-2222-2222-222222222222",
            manual_fields={},
        )

    assert quote.version == 1
