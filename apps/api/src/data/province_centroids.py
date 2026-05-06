"""Italian province (ISO 3166-2:IT) centroid coordinates.

Used by `places_prospector_service.search_places()` when the operator
selects only a province (no specific comune) — we anchor the Places
Nearby search on the provincial capital's coordinates.

The dataset covers all 110 Italian provinces (the 107 second-level
divisions plus AO + the four metropolitan cities that share a province
code). Coordinates are the lat/lng of the capoluogo (administrative
seat), source: Wikipedia / OpenStreetMap. Precision is 4 decimal
digits — sufficient for a 30-50km Places search radius.

Lookup is exposed via `province_centroid(code)` which returns
`(lat, lng)` or `None` for unknown codes (caller should fall back to
Places geocoding "Provincia di X, Italia").
"""

from __future__ import annotations

# Mapping: ISO 3166-2:IT province code → (lat, lng) of the capoluogo.
# Keys are uppercase 2-letter codes. All 110 entries below.
PROVINCE_CENTROIDS: dict[str, tuple[float, float]] = {
    "AG": (37.3105, 13.5765),  # Agrigento
    "AL": (44.9133, 8.6151),   # Alessandria
    "AN": (43.6158, 13.5189),  # Ancona
    "AO": (45.7370, 7.3199),   # Aosta
    "AP": (42.8536, 13.5751),  # Ascoli Piceno
    "AQ": (42.3498, 13.3996),  # L'Aquila
    "AR": (43.4633, 11.8796),  # Arezzo
    "AT": (44.9009, 8.2068),   # Asti
    "AV": (40.9145, 14.7906),  # Avellino
    "BA": (41.1171, 16.8719),  # Bari
    "BG": (45.6983, 9.6773),   # Bergamo
    "BI": (45.5631, 8.0586),   # Biella
    "BL": (46.1391, 12.2168),  # Belluno
    "BN": (41.1297, 14.7826),  # Benevento
    "BO": (44.4949, 11.3426),  # Bologna
    "BR": (40.6321, 17.9418),  # Brindisi
    "BS": (45.5416, 10.2118),  # Brescia
    "BT": (41.3243, 16.2690),  # Barletta-Andria-Trani (sede Andria)
    "BZ": (46.4982, 11.3548),  # Bolzano
    "CA": (39.2238, 9.1217),   # Cagliari
    "CB": (41.5601, 14.6620),  # Campobasso
    "CE": (41.0731, 14.3320),  # Caserta
    "CH": (42.3512, 14.1675),  # Chieti
    "CL": (37.4853, 14.0626),  # Caltanissetta
    "CN": (44.3841, 7.5424),   # Cuneo
    "CO": (45.8081, 9.0852),   # Como
    "CR": (45.1335, 10.0226),  # Cremona
    "CS": (39.2980, 16.2530),  # Cosenza
    "CT": (37.5079, 15.0830),  # Catania
    "CZ": (38.9098, 16.5874),  # Catanzaro
    "EN": (37.5663, 14.2795),  # Enna
    "FC": (44.2225, 12.0407),  # Forlì-Cesena (sede Forlì)
    "FE": (44.8378, 11.6195),  # Ferrara
    "FG": (41.4621, 15.5444),  # Foggia
    "FI": (43.7696, 11.2558),  # Firenze
    "FM": (43.1602, 13.7193),  # Fermo
    "FR": (41.6402, 13.3506),  # Frosinone
    "GE": (44.4056, 8.9463),   # Genova
    "GO": (45.9410, 13.6210),  # Gorizia
    "GR": (42.7596, 11.1124),  # Grosseto
    "IM": (43.8896, 8.0397),   # Imperia
    "IS": (41.5934, 14.2316),  # Isernia
    "KR": (39.0808, 17.1270),  # Crotone
    "LC": (45.8566, 9.3974),   # Lecco
    "LE": (40.3515, 18.1750),  # Lecce
    "LI": (43.5485, 10.3106),  # Livorno
    "LO": (45.3140, 9.5034),   # Lodi
    "LT": (41.4677, 12.9036),  # Latina
    "LU": (43.8429, 10.5027),  # Lucca
    "MB": (45.5845, 9.2744),   # Monza e Brianza
    "MC": (43.3007, 13.4530),  # Macerata
    "ME": (38.1938, 15.5540),  # Messina
    "MI": (45.4642, 9.1900),   # Milano
    "MN": (45.1564, 10.7914),  # Mantova
    "MO": (44.6471, 10.9252),  # Modena
    "MS": (44.0353, 10.1430),  # Massa-Carrara (sede Massa)
    "MT": (40.6669, 16.6043),  # Matera
    "NA": (40.8518, 14.2681),  # Napoli
    "NO": (45.4469, 8.6217),   # Novara
    "NU": (40.3214, 9.3293),   # Nuoro
    "OR": (39.9036, 8.5917),   # Oristano
    "PA": (38.1157, 13.3615),  # Palermo
    "PC": (45.0526, 9.6929),   # Piacenza
    "PD": (45.4064, 11.8768),  # Padova
    "PE": (42.4584, 14.2081),  # Pescara
    "PG": (43.1107, 12.3908),  # Perugia
    "PI": (43.7228, 10.4017),  # Pisa
    "PN": (45.9569, 12.6605),  # Pordenone
    "PO": (43.8777, 11.1024),  # Prato
    "PR": (44.8015, 10.3279),  # Parma
    "PT": (43.9335, 10.9176),  # Pistoia
    "PU": (43.9105, 12.9131),  # Pesaro e Urbino (sede Pesaro)
    "PV": (45.1847, 9.1582),   # Pavia
    "PZ": (40.6395, 15.8055),  # Potenza
    "RA": (44.4173, 12.1962),  # Ravenna
    "RC": (38.1110, 15.6473),  # Reggio Calabria
    "RE": (44.6982, 10.6306),  # Reggio Emilia
    "RG": (36.9268, 14.7263),  # Ragusa
    "RI": (42.4045, 12.8569),  # Rieti
    "RM": (41.9028, 12.4964),  # Roma
    "RN": (44.0594, 12.5765),  # Rimini
    "RO": (45.0707, 11.7895),  # Rovigo
    "SA": (40.6824, 14.7681),  # Salerno
    "SI": (43.3186, 11.3306),  # Siena
    "SO": (46.1697, 9.8728),   # Sondrio
    "SP": (44.1024, 9.8245),   # La Spezia
    "SR": (37.0755, 15.2866),  # Siracusa
    "SS": (40.7259, 8.5557),   # Sassari
    "SU": (39.2230, 8.8186),   # Sud Sardegna (sede Carbonia)
    "SV": (44.3079, 8.4810),   # Savona
    "TA": (40.4668, 17.2400),  # Taranto
    "TE": (42.6589, 13.7044),  # Teramo
    "TN": (46.0664, 11.1257),  # Trento
    "TO": (45.0703, 7.6869),   # Torino
    "TP": (38.0176, 12.5365),  # Trapani
    "TR": (42.5635, 12.6433),  # Terni
    "TS": (45.6495, 13.7768),  # Trieste
    "TV": (45.6669, 12.2430),  # Treviso
    "UD": (46.0626, 13.2345),  # Udine
    "VA": (45.8166, 8.8336),   # Varese
    "VB": (45.9221, 8.5512),   # Verbano-Cusio-Ossola (sede Verbania)
    "VC": (45.3208, 8.4180),   # Vercelli
    "VE": (45.4408, 12.3155),  # Venezia
    "VI": (45.5455, 11.5354),  # Vicenza
    "VR": (45.4384, 10.9916),  # Verona
    "VT": (42.4174, 12.1067),  # Viterbo
    "VV": (38.6757, 16.1018),  # Vibo Valentia
}


def province_centroid(code: str | None) -> tuple[float, float] | None:
    """Return `(lat, lng)` for a 2-letter Italian province code.

    Lookup is case-insensitive. Returns None for unknown / empty codes
    so the caller can fall back to Places Geocoding.
    """
    if not code:
        return None
    return PROVINCE_CENTROIDS.get(code.strip().upper())
