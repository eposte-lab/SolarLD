"""Synthetic Italian business profile generator — Atoka mock.

Used when ``ATOKA_MOCK_MODE=true`` so the full hunter funnel can be
tested end-to-end without a real Atoka key.

Design goals:
  - **Deterministic**: same (ateco_codes, province, index) always produces
    the same company — idempotent re-runs don't create phantom duplicates
    in scan_candidates because the DB upsert key is (tenant_id, scan_id,
    vat_number) and the VAT is stable.
  - **Realistic-looking**: Italian company names, real province coordinates,
    plausible firmographics (employees 10–199, revenue €500k–€5M).
  - **Clearly fake**: VATs start with ``IT9999`` — no real Italian company
    uses that prefix, so these rows won't collide with a future real import.
  - **Downstream-safe**: all fields downstream agents expect (hq_address,
    hq_lat, hq_lng, decision_maker_name, employees) are populated so
    L2 / L3 / L4 can run without skipping.

Changing the generator (e.g. adding more entries to the name pools)
does NOT break existing test records — VATs are hash-derived from
(ateco, province, index) and are stable across generator changes as
long as those three inputs stay the same.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..core.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Province data — centroid lat/lng + representative cities
# ---------------------------------------------------------------------------

_PROV: dict[str, dict[str, Any]] = {
    "AG": {"lat": 37.32, "lng": 13.58, "cap_pfx": "92", "cities": ["Agrigento", "Sciacca", "Canicattì"]},
    "AL": {"lat": 44.91, "lng": 8.62,  "cap_pfx": "15", "cities": ["Alessandria", "Tortona", "Novi Ligure"]},
    "AN": {"lat": 43.62, "lng": 13.50, "cap_pfx": "60", "cities": ["Ancona", "Senigallia", "Jesi"]},
    "AO": {"lat": 45.74, "lng": 7.32,  "cap_pfx": "11", "cities": ["Aosta"]},
    "AQ": {"lat": 42.35, "lng": 13.40, "cap_pfx": "67", "cities": ["L'Aquila", "Sulmona", "Avezzano"]},
    "AR": {"lat": 43.47, "lng": 11.88, "cap_pfx": "52", "cities": ["Arezzo", "Cortona", "Sansepolcro"]},
    "AT": {"lat": 44.90, "lng": 8.21,  "cap_pfx": "14", "cities": ["Asti", "Canelli", "Nizza Monferrato"]},
    "AV": {"lat": 40.91, "lng": 14.79, "cap_pfx": "83", "cities": ["Avellino", "Ariano Irpino", "Solofra"]},
    "BA": {"lat": 41.13, "lng": 16.87, "cap_pfx": "70", "cities": ["Bari", "Altamura", "Gravina", "Molfetta"]},
    "BG": {"lat": 45.70, "lng": 9.67,  "cap_pfx": "24", "cities": ["Bergamo", "Treviglio", "Caravaggio"]},
    "BI": {"lat": 45.56, "lng": 8.05,  "cap_pfx": "13", "cities": ["Biella", "Cossato"]},
    "BL": {"lat": 46.14, "lng": 12.22, "cap_pfx": "32", "cities": ["Belluno", "Feltre"]},
    "BN": {"lat": 41.13, "lng": 14.78, "cap_pfx": "82", "cities": ["Benevento", "Sant'Agata de' Goti"]},
    "BO": {"lat": 44.49, "lng": 11.34, "cap_pfx": "40", "cities": ["Bologna", "Imola", "Casalecchio"]},
    "BR": {"lat": 40.64, "lng": 17.94, "cap_pfx": "72", "cities": ["Brindisi", "Ostuni", "Fasano"]},
    "BS": {"lat": 45.54, "lng": 10.22, "cap_pfx": "25", "cities": ["Brescia", "Desenzano", "Chiari"]},
    "BT": {"lat": 41.20, "lng": 16.29, "cap_pfx": "76", "cities": ["Barletta", "Andria", "Trani"]},
    "BZ": {"lat": 46.50, "lng": 11.35, "cap_pfx": "39", "cities": ["Bolzano", "Merano", "Bressanone"]},
    "CA": {"lat": 39.22, "lng": 9.11,  "cap_pfx": "09", "cities": ["Cagliari", "Quartu", "Selargius"]},
    "CB": {"lat": 41.56, "lng": 14.66, "cap_pfx": "86", "cities": ["Campobasso", "Termoli"]},
    "CE": {"lat": 41.07, "lng": 14.33, "cap_pfx": "81", "cities": ["Caserta", "Aversa", "Capua"]},
    "CH": {"lat": 42.35, "lng": 14.17, "cap_pfx": "66", "cities": ["Chieti", "Lanciano", "Vasto"]},
    "CL": {"lat": 37.49, "lng": 14.06, "cap_pfx": "93", "cities": ["Caltanissetta", "Gela"]},
    "CN": {"lat": 44.39, "lng": 7.55,  "cap_pfx": "12", "cities": ["Cuneo", "Alba", "Saluzzo"]},
    "CO": {"lat": 45.81, "lng": 9.09,  "cap_pfx": "22", "cities": ["Como", "Cantù", "Erba"]},
    "CR": {"lat": 45.13, "lng": 10.03, "cap_pfx": "26", "cities": ["Cremona", "Crema"]},
    "CS": {"lat": 39.30, "lng": 16.25, "cap_pfx": "87", "cities": ["Cosenza", "Rende", "Rossano"]},
    "CT": {"lat": 37.50, "lng": 15.09, "cap_pfx": "95", "cities": ["Catania", "Acireale", "Paternò"]},
    "CZ": {"lat": 38.89, "lng": 16.60, "cap_pfx": "88", "cities": ["Catanzaro", "Lamezia Terme"]},
    "EN": {"lat": 37.57, "lng": 14.28, "cap_pfx": "94", "cities": ["Enna", "Piazza Armerina"]},
    "FC": {"lat": 44.22, "lng": 12.04, "cap_pfx": "47", "cities": ["Forlì", "Cesena"]},
    "FE": {"lat": 44.84, "lng": 11.62, "cap_pfx": "44", "cities": ["Ferrara", "Cento"]},
    "FG": {"lat": 41.46, "lng": 15.55, "cap_pfx": "71", "cities": ["Foggia", "Manfredonia", "Cerignola"]},
    "FI": {"lat": 43.77, "lng": 11.25, "cap_pfx": "50", "cities": ["Firenze", "Empoli", "Sesto Fiorentino"]},
    "FM": {"lat": 43.16, "lng": 13.72, "cap_pfx": "63", "cities": ["Fermo", "Porto San Giorgio"]},
    "FR": {"lat": 41.64, "lng": 13.35, "cap_pfx": "03", "cities": ["Frosinone", "Cassino", "Anagni"]},
    "GE": {"lat": 44.41, "lng": 8.93,  "cap_pfx": "16", "cities": ["Genova", "Rapallo", "Chiavari"]},
    "GO": {"lat": 45.94, "lng": 13.62, "cap_pfx": "34", "cities": ["Gorizia", "Monfalcone"]},
    "GR": {"lat": 42.76, "lng": 11.11, "cap_pfx": "58", "cities": ["Grosseto", "Orbetello"]},
    "IM": {"lat": 43.89, "lng": 7.92,  "cap_pfx": "18", "cities": ["Imperia", "Sanremo", "Ventimiglia"]},
    "IS": {"lat": 41.59, "lng": 14.23, "cap_pfx": "86", "cities": ["Isernia", "Venafro"]},
    "KR": {"lat": 39.08, "lng": 17.13, "cap_pfx": "88", "cities": ["Crotone", "Cirò Marina"]},
    "LC": {"lat": 45.86, "lng": 9.40,  "cap_pfx": "23", "cities": ["Lecco", "Merate"]},
    "LE": {"lat": 40.35, "lng": 18.17, "cap_pfx": "73", "cities": ["Lecce", "Copertino", "Galatina", "Nardò"]},
    "LI": {"lat": 43.54, "lng": 10.31, "cap_pfx": "57", "cities": ["Livorno", "Piombino", "Cecina"]},
    "LO": {"lat": 45.31, "lng": 9.50,  "cap_pfx": "26", "cities": ["Lodi", "Codogno"]},
    "LT": {"lat": 41.47, "lng": 12.90, "cap_pfx": "04", "cities": ["Latina", "Aprilia", "Terracina"]},
    "LU": {"lat": 43.84, "lng": 10.50, "cap_pfx": "55", "cities": ["Lucca", "Viareggio", "Capannori"]},
    "MB": {"lat": 45.58, "lng": 9.27,  "cap_pfx": "20", "cities": ["Monza", "Sesto San Giovanni", "Cinisello"]},
    "MC": {"lat": 43.30, "lng": 13.45, "cap_pfx": "62", "cities": ["Macerata", "Civitanova", "Tolentino"]},
    "ME": {"lat": 38.19, "lng": 15.55, "cap_pfx": "98", "cities": ["Messina", "Barcellona", "Milazzo"]},
    "MI": {"lat": 45.47, "lng": 9.19,  "cap_pfx": "20", "cities": ["Milano", "Sesto San Giovanni", "Cologno", "Legnano"]},
    "MN": {"lat": 45.16, "lng": 10.79, "cap_pfx": "46", "cities": ["Mantova", "Suzzara"]},
    "MO": {"lat": 44.65, "lng": 10.93, "cap_pfx": "41", "cities": ["Modena", "Carpi", "Sassuolo", "Mirandola"]},
    "MS": {"lat": 44.03, "lng": 10.14, "cap_pfx": "54", "cities": ["Massa", "Carrara"]},
    "MT": {"lat": 40.67, "lng": 16.60, "cap_pfx": "75", "cities": ["Matera", "Pisticci"]},
    "NA": {"lat": 40.85, "lng": 14.27, "cap_pfx": "80", "cities": ["Napoli", "Pozzuoli", "Torre del Greco", "Ercolano", "Portici", "Castellammare"]},
    "NO": {"lat": 45.45, "lng": 8.62,  "cap_pfx": "28", "cities": ["Novara", "Borgomanero", "Oleggio"]},
    "NU": {"lat": 40.32, "lng": 9.33,  "cap_pfx": "08", "cities": ["Nuoro", "Sassari"]},
    "OR": {"lat": 39.90, "lng": 8.59,  "cap_pfx": "09", "cities": ["Oristano"]},
    "PA": {"lat": 38.11, "lng": 13.35, "cap_pfx": "90", "cities": ["Palermo", "Bagheria", "Monreale", "Partinico"]},
    "PC": {"lat": 45.05, "lng": 9.70,  "cap_pfx": "29", "cities": ["Piacenza", "Castel San Giovanni"]},
    "PD": {"lat": 45.41, "lng": 11.88, "cap_pfx": "35", "cities": ["Padova", "Abano Terme", "Vigonza"]},
    "PE": {"lat": 42.46, "lng": 14.21, "cap_pfx": "65", "cities": ["Pescara", "Montesilvano", "Francavilla"]},
    "PG": {"lat": 43.11, "lng": 12.39, "cap_pfx": "06", "cities": ["Perugia", "Foligno", "Spoleto"]},
    "PI": {"lat": 43.72, "lng": 10.40, "cap_pfx": "56", "cities": ["Pisa", "Pontedera", "San Miniato"]},
    "PN": {"lat": 45.96, "lng": 12.66, "cap_pfx": "33", "cities": ["Pordenone", "Sacile"]},
    "PO": {"lat": 43.88, "lng": 11.10, "cap_pfx": "59", "cities": ["Prato"]},
    "PR": {"lat": 44.80, "lng": 10.33, "cap_pfx": "43", "cities": ["Parma", "Fidenza", "Salsomaggiore"]},
    "PT": {"lat": 43.93, "lng": 10.91, "cap_pfx": "51", "cities": ["Pistoia", "Montecatini", "Pescia"]},
    "PU": {"lat": 43.91, "lng": 12.91, "cap_pfx": "61", "cities": ["Pesaro", "Urbino", "Fano"]},
    "PV": {"lat": 45.19, "lng": 9.16,  "cap_pfx": "27", "cities": ["Pavia", "Vigevano", "Voghera"]},
    "PZ": {"lat": 40.64, "lng": 15.80, "cap_pfx": "85", "cities": ["Potenza", "Melfi", "Lagonegro"]},
    "RA": {"lat": 44.42, "lng": 12.21, "cap_pfx": "48", "cities": ["Ravenna", "Faenza", "Lugo"]},
    "RC": {"lat": 38.11, "lng": 15.65, "cap_pfx": "89", "cities": ["Reggio Calabria", "Palmi", "Gioia Tauro"]},
    "RE": {"lat": 44.70, "lng": 10.63, "cap_pfx": "42", "cities": ["Reggio Emilia", "Guastalla", "Scandiano"]},
    "RG": {"lat": 36.93, "lng": 14.73, "cap_pfx": "97", "cities": ["Ragusa", "Modica", "Vittoria"]},
    "RI": {"lat": 42.40, "lng": 12.86, "cap_pfx": "02", "cities": ["Rieti", "Poggio Mirteto"]},
    "RM": {"lat": 41.90, "lng": 12.49, "cap_pfx": "00", "cities": ["Roma", "Tivoli", "Frascati", "Civitavecchia", "Anzio"]},
    "RN": {"lat": 44.06, "lng": 12.57, "cap_pfx": "47", "cities": ["Rimini", "Riccione", "Santarcangelo"]},
    "RO": {"lat": 45.07, "lng": 11.79, "cap_pfx": "45", "cities": ["Rovigo", "Adria"]},
    "SA": {"lat": 40.68, "lng": 14.76, "cap_pfx": "84", "cities": ["Salerno", "Battipaglia", "Eboli", "Cava de' Tirreni"]},
    "SI": {"lat": 43.32, "lng": 11.33, "cap_pfx": "53", "cities": ["Siena", "Poggibonsi", "Montepulciano"]},
    "SO": {"lat": 46.17, "lng": 9.87,  "cap_pfx": "23", "cities": ["Sondrio", "Morbegno"]},
    "SP": {"lat": 44.10, "lng": 9.82,  "cap_pfx": "19", "cities": ["La Spezia", "Sarzana"]},
    "SR": {"lat": 37.08, "lng": 15.29, "cap_pfx": "96", "cities": ["Siracusa", "Augusta", "Lentini"]},
    "SS": {"lat": 40.73, "lng": 8.56,  "cap_pfx": "07", "cities": ["Sassari", "Alghero", "Porto Torres"]},
    "SU": {"lat": 39.37, "lng": 8.85,  "cap_pfx": "09", "cities": ["Carbonia", "Iglesias", "Villacidro"]},
    "SV": {"lat": 44.31, "lng": 8.48,  "cap_pfx": "17", "cities": ["Savona", "Albenga", "Finale Ligure"]},
    "TA": {"lat": 40.47, "lng": 17.24, "cap_pfx": "74", "cities": ["Taranto", "Manduria", "Grottaglie"]},
    "TE": {"lat": 42.66, "lng": 13.70, "cap_pfx": "64", "cities": ["Teramo", "Giulianova", "Roseto"]},
    "TN": {"lat": 46.07, "lng": 11.12, "cap_pfx": "38", "cities": ["Trento", "Rovereto", "Riva del Garda"]},
    "TO": {"lat": 45.07, "lng": 7.69,  "cap_pfx": "10", "cities": ["Torino", "Moncalieri", "Rivoli", "Collegno"]},
    "TP": {"lat": 38.02, "lng": 12.51, "cap_pfx": "91", "cities": ["Trapani", "Marsala", "Mazara del Vallo"]},
    "TR": {"lat": 42.56, "lng": 12.64, "cap_pfx": "05", "cities": ["Terni", "Orvieto"]},
    "TS": {"lat": 45.65, "lng": 13.78, "cap_pfx": "34", "cities": ["Trieste", "Muggia"]},
    "TV": {"lat": 45.67, "lng": 12.24, "cap_pfx": "31", "cities": ["Treviso", "Montebelluna", "Conegliano"]},
    "UD": {"lat": 46.07, "lng": 13.23, "cap_pfx": "33", "cities": ["Udine", "Pordenone", "Gorizia"]},
    "VA": {"lat": 45.82, "lng": 8.83,  "cap_pfx": "21", "cities": ["Varese", "Busto Arsizio", "Gallarate"]},
    "VB": {"lat": 45.92, "lng": 8.55,  "cap_pfx": "28", "cities": ["Verbania", "Domodossola"]},
    "VC": {"lat": 45.32, "lng": 8.42,  "cap_pfx": "13", "cities": ["Vercelli", "Borgosesia"]},
    "VE": {"lat": 45.44, "lng": 12.35, "cap_pfx": "30", "cities": ["Venezia", "Mestre", "Chioggia"]},
    "VI": {"lat": 45.55, "lng": 11.55, "cap_pfx": "36", "cities": ["Vicenza", "Bassano", "Schio"]},
    "VR": {"lat": 45.44, "lng": 10.99, "cap_pfx": "37", "cities": ["Verona", "Legnago", "San Bonifacio"]},
    "VS": {"lat": 39.50, "lng": 8.93,  "cap_pfx": "09", "cities": ["Villacidro", "Sanluri"]},
    "VT": {"lat": 42.42, "lng": 12.11, "cap_pfx": "01", "cities": ["Viterbo", "Civita Castellana"]},
    "VV": {"lat": 38.68, "lng": 16.10, "cap_pfx": "89", "cities": ["Vibo Valentia", "Pizzo"]},
}

_DEFAULT_PROV = {"lat": 42.50, "lng": 12.50, "cap_pfx": "00", "cities": ["Roma"]}

# ---------------------------------------------------------------------------
# Name pools (representative Italian surnames + first names)
# ---------------------------------------------------------------------------

_COGNOMI = [
    "Rossi", "Ferrari", "Russo", "Bianchi", "Romano", "Gallo", "Costa", "Fontana",
    "Conti", "Esposito", "Ricci", "Bruno", "Moretti", "Lombardi", "Barbieri",
    "Marino", "Greco", "Giordano", "Rizzo", "Mancini", "Pellegrini", "Caruso",
    "Ferretti", "Ferrara", "Cattaneo", "Marchetti", "Neri", "Santoro", "Amato",
    "Martinelli", "Grassi", "Milani", "Valentini", "Vitale", "Fabbri", "Palumbo",
    "De Luca", "Serra", "Gentile", "Caputo", "Montanari", "Basile", "Orlando",
]

_NOMI = [
    "Marco", "Giuseppe", "Antonio", "Giovanni", "Mario", "Luigi", "Luca",
    "Alessandro", "Francesco", "Roberto", "Stefano", "Andrea", "Paolo", "Angelo",
    "Davide", "Laura", "Anna", "Giulia", "Chiara", "Sara", "Martina", "Valentina",
]

_RUOLI = [
    "Amministratore Unico", "Amministratore Delegato", "Titolare",
    "Socio e Amministratore", "Direttore Generale", "Responsabile Commerciale",
]

_LEGAL_FORMS = [
    "SRL", "SRL", "SRL",  # weighted — most Italian SMEs are SRL
    "SPA", "SRLS", "SNC", "DI",
]

_STREET_TYPES = ["Via", "Via", "Via", "Corso", "Piazza", "Viale", "Via"]
_STREET_NAMES = [
    "Roma", "Garibaldi", "Mazzini", "Cavour", "Vittorio Emanuele", "Italia",
    "del Lavoro", "delle Industrie", "Nazionale", "Libertà", "della Repubblica",
    "Dante Alighieri", "dei Mille", "della Resistenza",
]


# ---------------------------------------------------------------------------
# ATECO sector keywords (first 2 digits of the code)
# ---------------------------------------------------------------------------

def _sector_keyword(ateco: str) -> str:
    try:
        prefix = int(ateco.split(".")[0] if "." in ateco else ateco[:2])
    except (ValueError, IndexError):
        return "Servizi"
    if 1 <= prefix <= 3:    return "Agricoltura"
    if 5 <= prefix <= 9:    return "Estrattive"
    if 10 <= prefix <= 12:  return "Alimentari"
    if 13 <= prefix <= 15:  return "Tessili"
    if 16 <= prefix <= 18:  return "Legno"
    if 19 <= prefix <= 23:  return "Chimici"
    if 24 <= prefix <= 25:  return "Metalli"
    if 26 <= prefix <= 28:  return "Meccanica"
    if 29 <= prefix <= 30:  return "Automotive"
    if 31 <= prefix <= 33:  return "Manifattura"
    if prefix == 35:        return "Energia"
    if 36 <= prefix <= 39:  return "Utilities"
    if 41 <= prefix <= 43:  return "Costruzioni"
    if 45 <= prefix <= 47:  return "Commercio"
    if 49 <= prefix <= 53:  return "Trasporti"
    if 55 <= prefix <= 56:  return "Ristorazione"
    if 58 <= prefix <= 63:  return "Informatica"
    if 64 <= prefix <= 66:  return "Finanza"
    if prefix == 68:        return "Immobiliare"
    if 69 <= prefix <= 75:  return "Professioni"
    if 77 <= prefix <= 82:  return "Servizi"
    return "Servizi"


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def generate_mock_atoka_profiles(
    ateco_codes: list[str],
    province_code: str = "NA",
    *,
    count: int = 20,
) -> list[dict[str, Any]]:
    """Return a list of dicts shaped like ``AtokaProfile`` kwargs.

    Profiles are deterministic: same inputs → same companies every time,
    so DB upserts on re-runs don't create duplicates.

    VATs use the ``IT9999`` prefix (clearly fake, never collides with
    real Italian P.IVA which start with province-specific codes).
    """
    # Import here to avoid circular deps at module import time
    from .italian_business_service import AtokaProfile  # noqa: PLC0415

    prov = province_code.upper().strip() or "NA"
    prov_data = _PROV.get(prov, _DEFAULT_PROV)
    base_lat: float = prov_data["lat"]
    base_lng: float = prov_data["lng"]
    cap_pfx: str = prov_data["cap_pfx"]
    cities: list[str] = prov_data["cities"]

    codes = ateco_codes if ateco_codes else ["41"]
    profiles: list[AtokaProfile] = []

    for i in range(count):
        ateco = codes[i % len(codes)]
        seed = f"{ateco}_{prov}_{i}"
        h = int(hashlib.md5(seed.encode()).hexdigest(), 16)  # 128-bit int

        # ── company identity ─────────────────────────────────────────────
        cognome      = _COGNOMI[h % len(_COGNOMI)]
        legal_form   = _LEGAL_FORMS[(h >> 8) % len(_LEGAL_FORMS)]
        sector       = _sector_keyword(ateco)
        legal_name   = f"{cognome} {sector} {legal_form}"

        # ── decision maker ───────────────────────────────────────────────
        nome  = _NOMI[(h >> 16) % len(_NOMI)]
        ruolo = _RUOLI[(h >> 20) % len(_RUOLI)]

        # ── firmographics ────────────────────────────────────────────────
        employees    = 10 + (h % 190)                    # 10–199
        revenue_eur  = 500_000 + ((h >> 24) % 4_500_000) # €500k–€5M

        # ── coordinates (centroid ± 0.15°, ≈ 15 km scatter) ─────────────
        lat = round(base_lat + ((h >> 32) % 300 - 150) / 1000.0, 5)
        lng = round(base_lng + ((h >> 40) % 300 - 150) / 1000.0, 5)

        # ── address ──────────────────────────────────────────────────────
        st_type = _STREET_TYPES[(h >> 48) % len(_STREET_TYPES)]
        st_name = _STREET_NAMES[(h >> 52) % len(_STREET_NAMES)]
        civico  = (h % 200) + 1
        city    = cities[(h >> 56) % len(cities)]
        cap     = f"{cap_pfx}{(h >> 60) % 1000:03d}"

        # ── VAT (IT9999 prefix → clearly mock) ───────────────────────────
        vat_digits = abs(h) % 10**5
        vat = f"IT9999{vat_digits:05d}{i:03d}"

        # ── website ──────────────────────────────────────────────────────
        domain = f"{cognome.lower().replace(' ', '')}{sector.lower().replace(' ', '')}.it"

        profiles.append(
            AtokaProfile(
                vat_number=vat,
                legal_name=legal_name,
                ateco_code=ateco,
                ateco_description=f"[MOCK] {sector}",
                yearly_revenue_cents=revenue_eur * 100,
                employees=employees,
                website_domain=domain,
                decision_maker_name=f"{nome} {cognome}",
                decision_maker_role=ruolo,
                linkedin_url=None,
                hq_address=f"{st_type} {st_name} {civico}",
                hq_cap=cap,
                hq_city=city,
                hq_province=prov,
                hq_lat=lat,
                hq_lng=lng,
                raw={"mock": True, "seed": seed, "atoka_mock_version": 1},
            )
        )

    log.info(
        "atoka_mock_profiles_generated",
        extra={
            "province": prov,
            "ateco_codes": codes,
            "count": len(profiles),
        },
    )
    return profiles
