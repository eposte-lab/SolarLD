"""Fase 0 ingest — normalise the CSEA energivori list into records.

Pure: exercises the row-parsing core (no file/openpyxl), with the real header
from the workbook and the critical P.IVA leading-zero handling.
"""

from __future__ import annotations

import pytest

from src.services.energivori_ingest import (
    EnergivoroRecord,
    normalize_piva,
    parse_energivori_rows,
)

# The real header from "Energivori 2026".
_HEADER = [
    "N.",
    "P.IVA",
    "Codice Fiscale",
    "Ragione Sociale",
    "Settore (stima)",
    "Priorita FV (stima)",
    "Classe",
    "TipoClasse",
    "Decorrenza",
    "Stato",
]


def test_normalize_piva_preserves_and_pads_leading_zeros() -> None:
    assert normalize_piva("00767880016") == "00767880016"
    # Source dropped the leading zeros (int) → padded back to 11.
    assert normalize_piva(767880016) == "00767880016"
    # Strips separators / prefixes.
    assert normalize_piva(" IT 00767880016 ") == "00767880016"
    # Rejects empty / too long.
    assert normalize_piva("") is None
    assert normalize_piva(None) is None
    assert normalize_piva("123456789012") is None  # 12 digits


def test_parse_maps_columns_and_carries_metadata() -> None:
    rows = [
        (
            1,
            "00767880016",
            "00767880016",
            "3T TRATTAMENTI TERMICI S.R.L.",
            "Trattamenti superficiali / galvanica",
            "Molto alta",
            "VALR1",
            "Elettro-intensita",
            "01/01/2026",
            "In istruttoria",
        ),
        (
            2,
            "00140850884",
            "00140850884",
            "AGRIPLAST SRL",
            "Plastica / gomma",
            "Molto alta",
            "VALR1",
            "Elettro-intensita",
            "01/01/2026",
            "Confermata",
        ),
    ]
    recs = parse_energivori_rows(_HEADER, rows)
    assert len(recs) == 2
    r0 = recs[0]
    assert isinstance(r0, EnergivoroRecord)
    assert r0.piva == "00767880016"
    assert r0.ragione_sociale.startswith("3T")
    assert r0.settore == "Trattamenti superficiali / galvanica"
    assert r0.priorita_fv == "Molto alta"
    assert recs[1].stato == "Confermata"


def test_parse_skips_invalid_and_dedupes() -> None:
    rows = [
        (1, "00140850884", "", "AGRIPLAST SRL", None, None, None, None, None, None),
        (2, "00140850884", "", "AGRIPLAST SRL (dup)", None, None, None, None, None, None),
        (3, "", "", "SENZA PIVA SRL", None, None, None, None, None, None),  # no P.IVA
        (4, "00767880016", "", "", None, None, None, None, None, None),  # no name
    ]
    recs = parse_energivori_rows(_HEADER, rows)
    assert [r.piva for r in recs] == ["00140850884"]  # dup collapsed, invalids dropped


def test_parse_is_column_order_independent() -> None:
    header = ["Ragione Sociale", "P.IVA", "Stato"]
    rows = [("ACME SRL", "00767880016", "Confermata")]
    recs = parse_energivori_rows(header, rows)
    assert recs[0].piva == "00767880016"
    assert recs[0].ragione_sociale == "ACME SRL"


def test_parse_raises_without_piva_column() -> None:
    with pytest.raises(ValueError, match="P.IVA"):
        parse_energivori_rows(["Ragione Sociale", "Stato"], [("ACME", "Confermata")])
