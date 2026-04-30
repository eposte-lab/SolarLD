"""Smoke tests for the WeasyPrint preventivo renderer.

Skipped automatically when ``weasyprint`` isn't installed (it has heavy
native deps — Pango, Cairo, GDK-Pixbuf — that we can't always install in
local pytest runs). CI installs them via the Dockerfile so the test
runs there.
"""

from __future__ import annotations

import pytest

weasyprint = pytest.importorskip("weasyprint")  # noqa: F841


# ---------------------------------------------------------------------------
# Filter unit tests — these don't need WeasyPrint at all.
# ---------------------------------------------------------------------------


def test_format_money_uses_italian_thousand_separator() -> None:
    from src.services.quote_pdf_renderer import _format_money

    assert _format_money(7531) == "7.531"
    assert _format_money(1234567) == "1.234.567"
    assert _format_money(0) == "0"
    assert _format_money(None) == "0"
    assert _format_money("garbage") == "0"


def test_format_decimal_italian_uses_comma() -> None:
    from src.services.quote_pdf_renderer import _format_decimal

    assert _format_decimal(3.14159, 2) == "3,14"
    assert _format_decimal(18.265, 1) == "18,3"


# ---------------------------------------------------------------------------
# End-to-end PDF render — the real test.
# ---------------------------------------------------------------------------


def _minimal_context() -> dict:
    """Just enough fields for the template to render without raising
    for missing vars. Anything not exercised here goes through Jinja's
    default-undefined handling (renders as empty string)."""
    return {
        "tenant_company_name": "SolarLED",
        "tenant_brand_color": "#0F766E",
        "tenant_brand_color_accent": "#F4A300",
        "tenant_logo_url": "",
        "tenant_piva": "01234567890",
        "tenant_telefono": "+39 010 1234567",
        "tenant_email": "info@solarled.it",
        "tenant_pec": "",
        "tenant_sede_legale": "Via Roma 1, Genova",
        "tenant_iscrizione_albo": "",
        "tenant_anni_esperienza": 12,
        "tenant_impianti_installati": 240,
        "azienda_ragione_sociale": "Officine Liguria SRL",
        "azienda_piva": "98765432101",
        "azienda_sede_operativa": "Via Roma 1, 16100 Genova",
        "azienda_sede_legale": "Via Roma 1, 16100 Genova",
        "azienda_decisore_nome": "Marco Rossi",
        "azienda_decisore_ruolo": "Amministratore",
        "azienda_settore": "Officine meccaniche",
        "preventivo_numero": "2026/PV/0001",
        "preventivo_data": "29 Aprile 2026",
        "preventivo_validita": "60 giorni",
        "commerciale_nome": "Luca Bianchi",
        "commerciale_ruolo": "Responsabile commerciale",
        "commerciale_email": "luca@solarled.it",
        "commerciale_telefono": "+39 333 1234567",
        "render_after_url": "",
        "solar_kw_installabili": 50.0,
        "solar_kwh_annui": 65000,
        "solar_m2_tetto": 320,
        "solar_pannelli_numero": 86,
        "solar_orientamento": "Sud",
        "solar_inclinazione": 15,
        "solar_irraggiamento_kwh_m2": 1450,
        "econ_consumo_stimato_kwh": 65000,
        "econ_costo_kwh_attuale": "0,22",
        "econ_costo_attuale_anno": 14300,
        "econ_copertura_perc": 65,
        "econ_risparmio_anno_1": 9295,
        "econ_risparmio_25_anni": 197519,
        "econ_payback_anni": 4.5,
        "econ_irr_25_anni": 370,
        "econ_co2_ton_anno": 18.3,
        "econ_co2_25_anni": 456,
        "econ_alberi_equivalenti": 870,
        "tech_marca_pannelli": "JA Solar",
        "tech_modello_pannelli": "JAM72D40 580W",
        "tech_potenza_singolo_pannello": "580 W",
        "tech_garanzia_pannelli_anni": 25,
        "tech_garanzia_produzione_anni": 30,
        "tech_marca_inverter": "Huawei",
        "tech_modello_inverter": "100KTL-M2",
        "tech_garanzia_inverter_anni": 10,
        "tech_struttura_montaggio": "K2 SystemSpeedRail",
        "tech_sistema_monitoraggio": "FusionSolar Cloud",
        "tech_accumulo_incluso": False,
        "prezzo_costo_impianto_lordo": 60000,
        "prezzo_iva_inclusa": False,
        "prezzo_aliquota_iva": 10,
        "prezzo_sconto_perc": 5,
        "prezzo_sconto_eur": 3000,
        "prezzo_finale": 57000,
        "incentivo_transizione_50_perc": 40,
        "incentivo_transizione_50_eur": 24000,
        "incentivo_iva_agevolata": True,
        "incentivo_super_ammortamento": 130,
        "incentivo_totale_eur": 24000,
        "costo_netto_post_incentivi": 33000,
        "pagamento_modalita_descrizione": "30/40/30",
        "pagamento_finanziamento_disponibile": True,
        "pagamento_finanziamento_descrizione": "Investimento 0",
        "tempi_progettazione_giorni": 15,
        "tempi_pratiche_giorni": 30,
        "tempi_installazione_giorni": 20,
        "tempi_collaudo_giorni": 10,
        "tempi_totale_giorni": 75,
        "note_aggiuntive": "Note di prova",
        "cashflow_years": [
            {
                "year_number": i,
                "kwh_produced": 65000,
                "savings_eur": 9295,
                "maintenance_cost": 0,
                "net_cashflow": 9295,
                "cumulative_cashflow": 9295 * i - 33000,
                "is_payback_year": i == 4,
            }
            for i in range(1, 26)
        ],
    }


def test_render_quote_pdf_produces_valid_pdf_bytes() -> None:
    from src.services.quote_pdf_renderer import render_quote_pdf

    pdf = render_quote_pdf(_minimal_context())

    # Magic-number check: PDF files start with "%PDF-".
    assert pdf[:4] == b"%PDF"
    # 7-page A4 with images and tables → easily > 10 KB even compressed.
    assert len(pdf) > 10_000
