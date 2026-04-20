"""Province → region lookups + haversine distance.

Italian `provincia` is a two-letter code (NA, MI, RM, ...) but the
`regional_incentives` table stores full region names ("Campania",
"Lombardia", ...). Scoring needs to match the two — this map is the
bridge.

The list below covers the 107 Italian provinces across 20 regions as of
the 2023 ISTAT codes. It is intentionally hard-coded rather than loaded
from a DB row because it changes once a decade at most.
"""

from __future__ import annotations

import math

PROVINCE_TO_REGION: dict[str, str] = {
    # Abruzzo
    "CH": "Abruzzo", "AQ": "Abruzzo", "PE": "Abruzzo", "TE": "Abruzzo",
    # Basilicata
    "MT": "Basilicata", "PZ": "Basilicata",
    # Calabria
    "CZ": "Calabria", "CS": "Calabria", "KR": "Calabria", "RC": "Calabria", "VV": "Calabria",
    # Campania
    "AV": "Campania", "BN": "Campania", "CE": "Campania", "NA": "Campania", "SA": "Campania",
    # Emilia-Romagna
    "BO": "Emilia-Romagna", "FC": "Emilia-Romagna", "FE": "Emilia-Romagna",
    "MO": "Emilia-Romagna", "PR": "Emilia-Romagna", "PC": "Emilia-Romagna",
    "RA": "Emilia-Romagna", "RE": "Emilia-Romagna", "RN": "Emilia-Romagna",
    # Friuli-Venezia Giulia
    "GO": "Friuli-Venezia Giulia", "PN": "Friuli-Venezia Giulia",
    "TS": "Friuli-Venezia Giulia", "UD": "Friuli-Venezia Giulia",
    # Lazio
    "FR": "Lazio", "LT": "Lazio", "RI": "Lazio", "RM": "Lazio", "VT": "Lazio",
    # Liguria
    "GE": "Liguria", "IM": "Liguria", "SP": "Liguria", "SV": "Liguria",
    # Lombardia
    "BG": "Lombardia", "BS": "Lombardia", "CO": "Lombardia", "CR": "Lombardia",
    "LC": "Lombardia", "LO": "Lombardia", "MN": "Lombardia", "MI": "Lombardia",
    "MB": "Lombardia", "PV": "Lombardia", "SO": "Lombardia", "VA": "Lombardia",
    # Marche
    "AN": "Marche", "AP": "Marche", "FM": "Marche", "MC": "Marche", "PU": "Marche",
    # Molise
    "CB": "Molise", "IS": "Molise",
    # Piemonte
    "AL": "Piemonte", "AT": "Piemonte", "BI": "Piemonte", "CN": "Piemonte",
    "NO": "Piemonte", "TO": "Piemonte", "VB": "Piemonte", "VC": "Piemonte",
    # Puglia
    "BA": "Puglia", "BT": "Puglia", "BR": "Puglia", "FG": "Puglia",
    "LE": "Puglia", "TA": "Puglia",
    # Sardegna
    "CA": "Sardegna", "NU": "Sardegna", "OR": "Sardegna", "SS": "Sardegna", "SU": "Sardegna",
    # Sicilia
    "AG": "Sicilia", "CL": "Sicilia", "CT": "Sicilia", "EN": "Sicilia",
    "ME": "Sicilia", "PA": "Sicilia", "RG": "Sicilia", "SR": "Sicilia", "TP": "Sicilia",
    # Toscana
    "AR": "Toscana", "FI": "Toscana", "GR": "Toscana", "LI": "Toscana",
    "LU": "Toscana", "MS": "Toscana", "PI": "Toscana", "PT": "Toscana",
    "PO": "Toscana", "SI": "Toscana",
    # Trentino-Alto Adige
    "BZ": "Trentino-Alto Adige", "TN": "Trentino-Alto Adige",
    # Umbria
    "PG": "Umbria", "TR": "Umbria",
    # Valle d'Aosta
    "AO": "Valle d'Aosta",
    # Veneto
    "BL": "Veneto", "PD": "Veneto", "RO": "Veneto", "TV": "Veneto",
    "VE": "Veneto", "VR": "Veneto", "VI": "Veneto",
}

_EARTH_RADIUS_KM = 6371.0088


def province_to_region(province: str | None) -> str | None:
    """Return the region name for a two-letter province code, or None.

    Matching is case-insensitive and whitespace-stripped. Returns ``None``
    for empty input or unknown codes — callers must handle that (we
    degrade scoring to a neutral value rather than raising).
    """
    if not province:
        return None
    key = province.strip().upper()
    return PROVINCE_TO_REGION.get(key)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two WGS84 points."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_KM * c
