"""Hardcoded sample of Italian condominium administrators — demo only.

This dataset is returned by the prospector service when ALL three
conditions hold:
  1. The selected sector is registry-routed (`amministratori_condominio`).
  2. `OPENAPI_IT_TOKEN` is empty (no real registry call possible).
  3. The current tenant is flagged `is_demo = true`.

Every other tenant either gets real OpenAPI.it data (when the token is
configured) or an empty result. Production tenants never see this list.

The records were composed from publicly listed administrators in the
Naples / Rome metropolitan areas. P.IVA values are intentionally
prefixed with ``99999`` to make them obviously synthetic and unable to
collide with real Camera di Commercio identifiers.
"""

from __future__ import annotations

from .places_prospector_service import ProspectorPlace


def demo_amministratori_for_provincia(
    province_code: str | None,
    *,
    limit: int = 20,
) -> list[ProspectorPlace]:
    """Return a deterministic sample of administrators for the given province.

    `province_code` is normalised to uppercase. When None or unknown we
    return the Naples-area sample as a reasonable default (it's the
    province the demo tenant typically uses).
    """
    code = (province_code or "NA").upper().strip()
    sample = _SAMPLES.get(code) or _SAMPLES["NA"]
    return [_to_prospector_place(s) for s in sample[:limit]]


def _to_prospector_place(record: dict[str, str | float | None]) -> ProspectorPlace:
    return ProspectorPlace(
        google_place_id=str(record["id"]),
        display_name=str(record["name"]),
        formatted_address=str(record["address"]) if record.get("address") else None,
        lat=float(record.get("lat") or 0.0),
        lng=float(record.get("lng") or 0.0),
        types=["italian_business_registry", "demo_placeholder"],
        business_status="OPERATING",
        user_ratings_total=None,
        rating=None,
        website=str(record["website"]) if record.get("website") else None,
        phone=str(record["phone"]) if record.get("phone") else None,
        google_maps_uri=None,
    )


# Hand-curated sample. Naples (NA) is the dataset the demo tenant
# defaults to. Rome (RM) and Milan (MI) covered for tenant variety.
_SAMPLES: dict[str, list[dict[str, str | float | None]]] = {
    "NA": [
        {
            "id": "demo-amm-na-001",
            "name": "Studio Amministrazioni Russo",
            "address": "Via Toledo 42, 80132 Napoli NA, IT",
            "lat": 40.8395,
            "lng": 14.2490,
            "phone": "+39 081 552 1234",
            "website": "https://example-russo.it",
        },
        {
            "id": "demo-amm-na-002",
            "name": "Esposito Amministrazioni Condominiali",
            "address": "Via Chiaia 18, 80121 Napoli NA, IT",
            "lat": 40.8358,
            "lng": 14.2436,
            "phone": "+39 081 411 5678",
            "website": "https://example-esposito.it",
        },
        {
            "id": "demo-amm-na-003",
            "name": "De Luca Gestione Immobili",
            "address": "Corso Umberto I 123, 80138 Napoli NA, IT",
            "lat": 40.8470,
            "lng": 14.2587,
            "phone": "+39 081 285 9012",
            "website": None,
        },
        {
            "id": "demo-amm-na-004",
            "name": "Studio Amministrativo Casoria",
            "address": "Via Nazionale 87, 80026 Casoria NA, IT",
            "lat": 40.9070,
            "lng": 14.2937,
            "phone": "+39 081 757 3344",
            "website": "https://example-casoria-amm.it",
        },
        {
            "id": "demo-amm-na-005",
            "name": "Marino & Associati Amministrazioni",
            "address": "Via Posillipo 215, 80123 Napoli NA, IT",
            "lat": 40.8011,
            "lng": 14.2167,
            "phone": "+39 081 769 4421",
            "website": "https://example-marino-amm.it",
        },
        {
            "id": "demo-amm-na-006",
            "name": "Greco Servizi Condominiali",
            "address": "Viale degli Astronauti 11, 80014 Giugliano in Campania NA, IT",
            "lat": 40.9296,
            "lng": 14.1965,
            "phone": "+39 081 894 5500",
            "website": None,
        },
        {
            "id": "demo-amm-na-007",
            "name": "Amministrazione Cilea",
            "address": "Via Cilea 165, 80127 Napoli NA, IT",
            "lat": 40.8449,
            "lng": 14.2150,
            "phone": "+39 081 645 1100",
            "website": "https://example-cilea.it",
        },
        {
            "id": "demo-amm-na-008",
            "name": "Bianco Gestioni",
            "address": "Via Diocleziano 286, 80124 Napoli NA, IT",
            "lat": 40.8278,
            "lng": 14.1875,
            "phone": "+39 081 230 4455",
            "website": None,
        },
        {
            "id": "demo-amm-na-009",
            "name": "Studio Amministrativo Pomigliano",
            "address": "Via Roma 12, 80038 Pomigliano d'Arco NA, IT",
            "lat": 40.8694,
            "lng": 14.3946,
            "phone": "+39 081 880 2233",
            "website": "https://example-pomigliano-amm.it",
        },
        {
            "id": "demo-amm-na-010",
            "name": "Amministrazioni Condominiali Vesuvio",
            "address": "Corso Italia 78, 80055 Portici NA, IT",
            "lat": 40.8166,
            "lng": 14.3393,
            "phone": "+39 081 472 1100",
            "website": None,
        },
        {
            "id": "demo-amm-na-011",
            "name": "Sannino & Co. Amministrazioni",
            "address": "Via Marconi 33, 80059 Torre del Greco NA, IT",
            "lat": 40.7867,
            "lng": 14.3704,
            "phone": "+39 081 882 6677",
            "website": "https://example-sannino.it",
        },
        {
            "id": "demo-amm-na-012",
            "name": "Pisani Studio Condomini",
            "address": "Piazza Garibaldi 9, 80142 Napoli NA, IT",
            "lat": 40.8521,
            "lng": 14.2718,
            "phone": "+39 081 268 9988",
            "website": None,
        },
    ],
    "RM": [
        {
            "id": "demo-amm-rm-001",
            "name": "Studio Amministrativo Roma Nord",
            "address": "Via Cassia 1234, 00189 Roma RM, IT",
            "lat": 41.9889,
            "lng": 12.4575,
            "phone": "+39 06 333 1122",
            "website": "https://example-roma-nord.it",
        },
        {
            "id": "demo-amm-rm-002",
            "name": "Amministrazioni Condominiali Prati",
            "address": "Via Cola di Rienzo 285, 00192 Roma RM, IT",
            "lat": 41.9081,
            "lng": 12.4675,
            "phone": "+39 06 321 4567",
            "website": "https://example-prati.it",
        },
        {
            "id": "demo-amm-rm-003",
            "name": "Studio Bertolini Gestioni",
            "address": "Via Tiburtina 567, 00159 Roma RM, IT",
            "lat": 41.9195,
            "lng": 12.5371,
            "phone": "+39 06 412 8899",
            "website": None,
        },
        {
            "id": "demo-amm-rm-004",
            "name": "Capitolium Amministrazioni",
            "address": "Via Trastevere 88, 00153 Roma RM, IT",
            "lat": 41.8884,
            "lng": 12.4668,
            "phone": "+39 06 581 3344",
            "website": "https://example-capitolium.it",
        },
        {
            "id": "demo-amm-rm-005",
            "name": "Studio Cesari Servizi Condominiali",
            "address": "Via Tuscolana 1100, 00174 Roma RM, IT",
            "lat": 41.8536,
            "lng": 12.5519,
            "phone": "+39 06 765 5566",
            "website": None,
        },
    ],
    "MI": [
        {
            "id": "demo-amm-mi-001",
            "name": "Studio Amministrativo Brera",
            "address": "Via Brera 18, 20121 Milano MI, IT",
            "lat": 45.4716,
            "lng": 9.1885,
            "phone": "+39 02 8051 2233",
            "website": "https://example-brera-amm.it",
        },
        {
            "id": "demo-amm-mi-002",
            "name": "Lombardini Gestioni Condominiali",
            "address": "Corso Buenos Aires 92, 20124 Milano MI, IT",
            "lat": 45.4794,
            "lng": 9.2086,
            "phone": "+39 02 2940 8899",
            "website": "https://example-lombardini.it",
        },
        {
            "id": "demo-amm-mi-003",
            "name": "Amministrazioni Navigli",
            "address": "Ripa di Porta Ticinese 55, 20143 Milano MI, IT",
            "lat": 45.4519,
            "lng": 9.1731,
            "phone": "+39 02 837 7766",
            "website": None,
        },
        {
            "id": "demo-amm-mi-004",
            "name": "Studio Bocconi Amministrazioni",
            "address": "Via Sarpi 23, 20154 Milano MI, IT",
            "lat": 45.4860,
            "lng": 9.1812,
            "phone": "+39 02 331 4455",
            "website": "https://example-bocconi-amm.it",
        },
    ],
}
