"""Pure-function tests for practice_extraction_service.

The Claude Vision call itself isn't tested here (it requires the live
API + an image). What we exercise:

  • _parse_envelope — JSON tolerance for code fences, missing keys,
    confidence range
  • _clean_fields — Italian field normalisation (POD/CF/PIVA/provincia)
  • build_apply_payload — the routing matrix that decides which fields
    land on tenant vs subject vs practice vs extras

These are the pieces the worker + apply route depend on; if they
regress the wrong fields end up in the wrong table.
"""

from __future__ import annotations

import pytest

from src.services.practice_extraction_service import (
    PROMPTS,
    _clean_fields,
    _parse_envelope,
    build_apply_payload,
)


# ---------------------------------------------------------------------------
# _parse_envelope
# ---------------------------------------------------------------------------


def test_parse_envelope_accepts_clean_json() -> None:
    text = '{"fields": {"pod": "IT001E12345678"}, "confidence": 0.92, "notes": "ok"}'
    parsed = _parse_envelope(text)
    assert parsed is not None
    assert parsed["fields"]["pod"] == "IT001E12345678"
    assert parsed["confidence"] == 0.92


def test_parse_envelope_strips_code_fences() -> None:
    text = '```json\n{"fields": {"a": 1}, "confidence": 0.5}\n```'
    parsed = _parse_envelope(text)
    assert parsed is not None
    assert parsed["fields"] == {"a": 1}


def test_parse_envelope_rejects_missing_fields_key() -> None:
    assert _parse_envelope('{"confidence": 0.9}') is None


def test_parse_envelope_rejects_missing_confidence() -> None:
    assert _parse_envelope('{"fields": {}}') is None


def test_parse_envelope_rejects_non_dict_fields() -> None:
    assert _parse_envelope('{"fields": [], "confidence": 0.5}') is None


def test_parse_envelope_rejects_out_of_range_confidence() -> None:
    assert _parse_envelope('{"fields": {}, "confidence": 1.5}') is None
    assert _parse_envelope('{"fields": {}, "confidence": -0.1}') is None


def test_parse_envelope_rejects_invalid_json() -> None:
    assert _parse_envelope("not json at all") is None
    assert _parse_envelope("") is None


def test_parse_envelope_rejects_top_level_array() -> None:
    assert _parse_envelope("[1,2,3]") is None


# ---------------------------------------------------------------------------
# _clean_fields
# ---------------------------------------------------------------------------


def test_clean_fields_uppercases_pod_and_strips_spaces() -> None:
    out = _clean_fields({"pod": " it001e 1234 5678 "})
    assert out["pod"] == "IT001E12345678"


def test_clean_fields_normalises_partita_iva_to_digits_only() -> None:
    # Trailing dots / spaces are common artefacts of OCR.
    out = _clean_fields({"partita_iva": "IT 12345678901."})
    assert out["partita_iva"] == "12345678901"


def test_clean_fields_uppercases_codice_fiscale_variants() -> None:
    out = _clean_fields(
        {
            "codice_fiscale": " rsslcu80a01h501x ",
            "owner_codice_fiscale": " mrtgvn85b15f205z ",
            "intestatario_codice_fiscale": "rssgnn70t05f205q",
        }
    )
    assert out["codice_fiscale"] == "RSSLCU80A01H501X"
    assert out["owner_codice_fiscale"] == "MRTGVN85B15F205Z"
    assert out["intestatario_codice_fiscale"] == "RSSGNN70T05F205Q"


def test_clean_fields_truncates_provincia_to_two_chars() -> None:
    out = _clean_fields(
        {
            "sede_legale_provincia": "milano",
            "residenza_provincia": "rm",
        }
    )
    assert out["sede_legale_provincia"] == "MI"
    assert out["residenza_provincia"] == "RM"


def test_clean_fields_replaces_empty_strings_with_none() -> None:
    out = _clean_fields({"foglio": "   ", "particella": "12"})
    assert out["foglio"] is None
    assert out["particella"] == "12"


def test_clean_fields_preserves_none_values() -> None:
    out = _clean_fields({"subalterno": None})
    assert out["subalterno"] is None


def test_clean_fields_passes_through_non_strings() -> None:
    out = _clean_fields({"potenza_disponibile_kw": 4.5, "count": 3})
    assert out["potenza_disponibile_kw"] == 4.5
    assert out["count"] == 3


# ---------------------------------------------------------------------------
# build_apply_payload — routing matrix
# ---------------------------------------------------------------------------


def test_apply_visura_cciaa_to_subject_default() -> None:
    """Default routing for visura_cciaa is the cliente (subject)."""
    fields = {
        "ragione_sociale": "ACME Energy Srl",
        "partita_iva": "12345678901",
        "codice_fiscale": "12345678901",
        "sede_legale_indirizzo": "Via Roma 1",
        "sede_legale_citta": "Milano",
        "sede_legale_provincia": "MI",
        "sede_legale_cap": "20100",
        "codice_ateco": "43.21.01",
        "legale_rappresentante_nome": "Mario",
        "legale_rappresentante_cognome": "Rossi",
        "legale_rappresentante_codice_fiscale": "RSSMRA80A01H501X",
    }
    out = build_apply_payload("visura_cciaa", fields)
    assert "tenant" not in out
    assert out["subject"] == {
        "business_name": "ACME Energy Srl",
        "vat_number": "12345678901",
        "codice_fiscale": "12345678901",
        "legal_address": "Via Roma 1",
        "legal_city": "Milano",
        "legal_province": "MI",
        "legal_cap": "20100",
        "ateco": "43.21.01",
        "owner_first_name": "Mario",
        "owner_last_name": "Rossi",
        "owner_codice_fiscale": "RSSMRA80A01H501X",
    }


def test_apply_visura_cciaa_to_tenant_when_requested() -> None:
    """Operator self-onboarding case — visura belongs to the installer."""
    fields = {
        "ragione_sociale": "Sole Energy Srl",
        "partita_iva": "98765432101",
        "codice_fiscale": "98765432101",
        "numero_cciaa": "MI-1234567",
        "sede_legale_indirizzo": "Via Solare 5",
        # Subject-only field — must not appear on tenant payload.
        "codice_ateco": "43.21.01",
    }
    out = build_apply_payload("visura_cciaa", fields, visura_target="tenant")
    assert "subject" not in out
    assert out["tenant"] == {
        "business_name": "Sole Energy Srl",
        "vat_number": "98765432101",
        "codice_fiscale": "98765432101",
        "numero_cciaa": "MI-1234567",
        "legal_address": "Via Solare 5",
    }


def test_apply_visura_catastale_splits_practice_columns_and_extras() -> None:
    fields = {
        "foglio": "127",
        "particella": "456",
        "subalterno": "3",
        "comune": "Milano",
        "provincia": "MI",
        "categoria_catastale": "A/2",
        "rendita_catastale": 850.0,
        "intestatario_nome_cognome": "Rossi Mario",
        "intestatario_codice_fiscale": "RSSMRA80A01H501X",
        "quota_possesso": "1/1",
    }
    out = build_apply_payload("visura_catastale", fields)
    assert out["practice"] == {
        "catastale_foglio": "127",
        "catastale_particella": "456",
        "catastale_subalterno": "3",
    }
    assert out["extras"] == {
        "catastale_comune": "Milano",
        "catastale_provincia": "MI",
        "catastale_categoria": "A/2",
        "catastale_rendita": 850.0,
        "catastale_intestatario": "Rossi Mario",
        "catastale_intestatario_cf": "RSSMRA80A01H501X",
        "catastale_quota": "1/1",
    }
    assert "tenant" not in out
    assert "subject" not in out


def test_apply_documento_identita_targets_subject_only() -> None:
    fields = {
        "nome": "Mario",
        "cognome": "Rossi",
        "codice_fiscale": "RSSMRA80A01H501X",
        "data_nascita": "1980-01-01",
        "luogo_nascita": "Milano",
        "residenza_indirizzo": "Via Verdi 10",
        "residenza_cap": "20100",
        "residenza_citta": "Milano",
        "residenza_provincia": "MI",
        # Fields not in the target map — ignored.
        "tipo_documento": "carta_identita",
        "numero_documento": "AB1234567",
    }
    out = build_apply_payload("documento_identita", fields)
    assert set(out.keys()) == {"subject"}
    assert out["subject"]["owner_first_name"] == "Mario"
    assert out["subject"]["owner_last_name"] == "Rossi"
    assert out["subject"]["owner_codice_fiscale"] == "RSSMRA80A01H501X"
    assert out["subject"]["residence_province"] == "MI"
    # Document number is intentionally NOT applied (no schema field for it).
    assert "numero_documento" not in out["subject"]


def test_apply_bolletta_pod_splits_practice_and_extras() -> None:
    fields = {
        "pod": "IT001E12345678",
        "distributore": "areti",
        "tensione_alimentazione": "BT",
        "potenza_disponibile_kw": 6.0,
        "potenza_impegnata_kw": 4.5,
        "indirizzo_fornitura_via": "Via Roma 1",
        "indirizzo_fornitura_cap": "00100",
        "indirizzo_fornitura_citta": "Roma",
        "indirizzo_fornitura_provincia": "RM",
    }
    out = build_apply_payload("bolletta_pod", fields)
    assert out["practice"] == {
        "impianto_pod": "IT001E12345678",
        "impianto_distributore": "areti",
    }
    assert out["extras"] == {
        "bolletta_tensione": "BT",
        "bolletta_potenza_disponibile": 6.0,
        "bolletta_potenza_impegnata": 4.5,
        "bolletta_indirizzo": "Via Roma 1",
        "bolletta_cap": "00100",
        "bolletta_citta": "Roma",
        "bolletta_provincia": "RM",
    }


def test_apply_altro_returns_empty_payload() -> None:
    """`altro` is intentionally inert — operator must transcribe."""
    out = build_apply_payload("altro", {"intestatario_nome_cognome": "Mario"})
    assert out == {}


def test_apply_skips_none_and_empty_string_fields() -> None:
    fields = {
        "pod": "IT001E12345678",
        "distributore": None,
        "tensione_alimentazione": "",
        "potenza_disponibile_kw": 0,  # zero is a real value — must persist
    }
    out = build_apply_payload("bolletta_pod", fields)
    assert out["practice"] == {"impianto_pod": "IT001E12345678"}
    # Zero is a legitimate value (e.g. very low contract); it must NOT be
    # filtered out alongside None / empty string.
    assert out["extras"] == {"bolletta_potenza_disponibile": 0}


def test_apply_unknown_kind_returns_empty_payload() -> None:
    out = build_apply_payload("totally_made_up_kind", {"pod": "IT001"})
    assert out == {}


# ---------------------------------------------------------------------------
# PROMPTS registry — pin the kinds the worker accepts.
# ---------------------------------------------------------------------------


def test_prompts_registry_covers_all_supported_kinds() -> None:
    """If a new kind is added the worker must learn to dispatch it.
    Pinning the set here forces the CHECK constraint in migration 0086,
    PROMPTS, the route's VALID_UPLOAD_KINDS, and build_apply_payload
    to stay in sync.
    """
    assert set(PROMPTS.keys()) == {
        "visura_cciaa",
        "visura_catastale",
        "documento_identita",
        "bolletta_pod",
        "altro",
    }


@pytest.mark.parametrize(
    "kind",
    [
        "visura_cciaa",
        "visura_catastale",
        "documento_identita",
        "bolletta_pod",
        "altro",
    ],
)
def test_each_prompt_includes_envelope_instruction(kind: str) -> None:
    """Every prompt must instruct Claude to return the
    {fields, confidence, notes} envelope — otherwise _parse_envelope
    will reject the response."""
    text = PROMPTS[kind]
    assert '"fields"' in text
    assert '"confidence"' in text
