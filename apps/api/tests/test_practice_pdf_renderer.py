"""Smoke tests for the WeasyPrint practice renderer.

Skipped automatically when ``weasyprint`` isn't installed (heavy native
deps). CI installs them via the Dockerfile so the tests run there.
"""

from __future__ import annotations

import pytest

weasyprint = pytest.importorskip("weasyprint")  # noqa: F841


# ---------------------------------------------------------------------------
# Pure helper: registry membership.
# ---------------------------------------------------------------------------


def test_supported_template_codes_match_sprint_2_set() -> None:
    """Pin the supported templates explicitly so adding a new
    template requires updating this assertion deliberately.

    Sprint 1: dm_37_08, comunicazione_comune.
    Sprint 2: + modello_unico_p1, modello_unico_p2, schema_unifilare,
              attestazione_titolo, tica_areti, transizione_50_*.
    """
    from src.services.practice_pdf_renderer import SUPPORTED_TEMPLATE_CODES

    assert SUPPORTED_TEMPLATE_CODES == frozenset(
        {
            "dm_37_08",
            "comunicazione_comune",
            "modello_unico_p1",
            "modello_unico_p2",
            "schema_unifilare",
            "attestazione_titolo",
            "tica_areti",
            "transizione_50_ex_ante",
            "transizione_50_ex_post",
            "transizione_50_attestazione",
        }
    )


def test_render_practice_pdf_rejects_unknown_template_code() -> None:
    from src.services.practice_pdf_renderer import render_practice_pdf

    with pytest.raises(ValueError, match="unsupported|template"):
        render_practice_pdf("nonexistent_doc", _minimal_context())


# ---------------------------------------------------------------------------
# End-to-end render — both Sprint 1 templates.
# ---------------------------------------------------------------------------


def _minimal_context() -> dict:
    """Just enough for both templates to render. The mapper fills in
    way more in production; here we only set what the templates
    actually look up. Anything missing falls back via Jinja default
    undefined → "" (templates use ``or "—"``)."""
    return {
        "tenant": {
            "ragione_sociale": "Acme Energy SRL",
            "business_name": "Acme Energy",
            "piva": "01234567890",
            "codice_fiscale": "01234567890",
            "numero_cciaa": "MI-1234567",
            "sede_legale": "Via Roma 1, 20121 Milano",
            "sede_operativa": "Via Roma 1, 20121 Milano",
            "email": "info@acme.it",
            "telefono": "+39 02 1234567",
            "pec": "acme@pec.it",
            "logo_url": "",
            "brand_color": "#0F766E",
            "brand_color_accent": "#F4A300",
            "responsabile_tecnico_nome": "Mario",
            "responsabile_tecnico_cognome": "Rossi",
            "responsabile_tecnico_nome_completo": "Mario Rossi",
            "responsabile_tecnico_codice_fiscale": "RSSMRA80A01H501Z",
            "responsabile_tecnico_qualifica": "Ingegnere",
            "responsabile_tecnico_iscrizione_albo": "Ordine Ingegneri Milano n. 1234",
        },
        "installatore": {
            "ragione_sociale": "Acme Energy SRL",
            "piva": "01234567890",
            "codice_fiscale": "01234567890",
            "cciaa": "MI-1234567",
            "sede": "Via Roma 1, 20121 Milano",
            "telefono": "+39 02 1234567",
            "email": "info@acme.it",
            "pec": "acme@pec.it",
            "responsabile_tecnico": {
                "nome_completo": "Mario Rossi",
                "codice_fiscale": "RSSMRA80A01H501Z",
                "qualifica": "Ingegnere",
                "iscrizione_albo": "Ordine Ingegneri Milano n. 1234",
            },
        },
        "cliente": {
            "ragione_sociale": "Cliente SRL",
            "piva": "98765432101",
            "codice_fiscale": "98765432101",
            "sede": "Via Milano 5, 20100 Milano",
        },
        "decisore": {
            "nome_completo": "Luca Bianchi",
            "ruolo": "Amministratore",
            "codice_fiscale": "BNCLCU80A01H501Z",
        },
        "impianto": {
            "potenza_kw": 50.0,
            "pannelli_count": 86,
            "pod": "IT001E12345678",
            "distributore": "E-Distribuzione S.p.A.",
            "data_inizio_lavori": "2026-04-01",
            "data_fine_lavori": "2026-04-30",
        },
        "componenti": {
            "pannelli": {
                "marca": "JA Solar",
                "modello": "JAM72D40 580W",
                "potenza_w": "580 W",
                "quantita": 86,
            },
            "inverter": {
                "marca": "Huawei",
                "modello": "100KTL-M2",
                "potenza_kw": "100 kW",
                "quantita": 1,
            },
            "accumulo": {
                "presente": False,
            },
        },
        "ubicazione": {
            "indirizzo": "Via Roma 1",
            "comune": "Milano",
            "provincia": "MI",
            "cap": "20121",
            "catastale_foglio": "123",
            "catastale_particella": "45",
            "catastale_subalterno": "1",
        },
        "energetico": {
            "kwp": 50.0,
            "kwh_anno": 65000,
            "risparmio_anno_1": 9295,
        },
        "pratica": {
            "numero": "ACME/2026/0001",
            "data_apertura": "30/04/2026",
            "data_documento": "30/04/2026",
        },
        "norme": {
            "dm_37_08": "DM 22 gennaio 2008 n. 37 — Riordino…",
            "cei_0_21": "CEI 0-21 — Regola tecnica di riferimento BT",
            "cei_0_16": "CEI 0-16 — Regola tecnica di riferimento AT/MT",
            "cei_82_25": "CEI 82-25 — Sistemi fotovoltaici",
            "uni_8290": "UNI 8290 — Edilizia residenziale",
            "dlgs_28_2011": "D.Lgs. 3 marzo 2011 n. 28 — Fonti rinnovabili",
        },
        # Sprint 2: extras blob — keys documented in
        # practice_data_mapper.EXTRAS_SHAPE.
        "extras": {
            "iban": "IT60X0542811101000000123456",
            "regime_ritiro": "gse_po",
            "regime_ritiro_label": "Ritiro Dedicato GSE",
            "qualita_richiedente": "proprietario",
            "qualita_richiedente_label": "Proprietario",
            "denominazione_impianto": "FV Acme HQ",
            "tipologia_struttura": "edificio",
            "tipologia_struttura_label": "Edificio esistente",
            "codice_identificativo_connessione": "12345678",
            "codice_rintracciabilita": "ABCD1234",
            "potenza_immissione_kw": 50.0,
            "configurazione_accumulo": "lato_dc",
            "configurazione_accumulo_label": "Lato DC monofase",
            "utente_dispacciamento": {},
            "transizione50": {
                "ateco": "27.11.00",
                "tep_anno": 12.345,
                "percentuale_riduzione": 7.8,
                "fascia_agevolativa": "Fascia 2",
                "investimento_totale_eur": 75000,
                "certificatore_nome": "Mario Rossi",
                "certificatore_albo": "Ordine Ingegneri Milano n. 1234",
                "perito_nome": "Giulia Bianchi",
                "perito_albo": "Tribunale di Milano",
                "revisore_nome": "Studio Verdi & Partners",
                "revisore_registro": "Reg. revisori n. 12345",
            },
        },
    }


def test_render_dm_37_08_produces_valid_pdf_bytes() -> None:
    from src.services.practice_pdf_renderer import render_practice_pdf

    pdf = render_practice_pdf("dm_37_08", _minimal_context())

    # Magic-number check — PDFs always start with "%PDF-".
    assert pdf[:4] == b"%PDF"
    # 3-page A4 with header + tables → comfortably > 5 KB.
    assert len(pdf) > 5_000


def test_render_comunicazione_comune_produces_valid_pdf_bytes() -> None:
    from src.services.practice_pdf_renderer import render_practice_pdf

    pdf = render_practice_pdf("comunicazione_comune", _minimal_context())

    assert pdf[:4] == b"%PDF"
    # Single-page comunicazione is small but still >2 KB compressed.
    assert len(pdf) > 2_000


# ---------------------------------------------------------------------------
# Sprint 2 — smoke render every new template with the same minimal context.
# We only assert magic-number + size: the goal is to catch Jinja syntax
# errors and missing context keys at CI time. Visual regressions are out
# of scope for unit tests (template review happens manually).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template_code",
    [
        "modello_unico_p1",
        "modello_unico_p2",
        "schema_unifilare",
        "attestazione_titolo",
        "tica_areti",
        "transizione_50_ex_ante",
        "transizione_50_ex_post",
        "transizione_50_attestazione",
    ],
)
def test_render_sprint2_template_produces_valid_pdf_bytes(template_code: str) -> None:
    from src.services.practice_pdf_renderer import render_practice_pdf

    pdf = render_practice_pdf(template_code, _minimal_context())

    assert pdf[:4] == b"%PDF"
    # All Sprint 2 templates produce at least 2 KB compressed.
    assert len(pdf) > 2_000
