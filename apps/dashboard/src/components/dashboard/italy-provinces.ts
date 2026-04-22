/**
 * Italian province centroids — all 107 ISTAT province codes.
 *
 * Coordinates are approximate geographic centres used solely for map
 * marker placement. They don't need survey accuracy — 0.1° precision
 * (≈10 km) is sufficient for visual clustering.
 *
 * Source: ISTAT 2023 administrative boundaries, centroid approximations.
 *
 * Note on Sardinian provinces: Carbonia-Iglesias (CI) and
 * Medio-Campidano (VS) were merged into Sud Sardegna (SU) in 2016;
 * Olbia-Tempio (OT) and Ogliastra (OG) were merged into Sassari (SS)
 * and Nuoro (NU) respectively. Legacy codes are retained here as
 * fallbacks so that leads with old province data still render.
 */

export interface ProvinceCentroid {
  lat: number;
  lng: number;
  name: string;
  /** NUTS-2 region code for grouping (e.g. "ITC4" = Lombardia). */
  region?: string;
}

export const PROVINCE_CENTROIDS: Record<string, ProvinceCentroid> = {
  // ── Piemonte ────────────────────────────────────────────────────────────
  AL: { lat: 44.91, lng: 8.62,  name: 'Alessandria' },
  AT: { lat: 44.90, lng: 8.21,  name: 'Asti' },
  BI: { lat: 45.56, lng: 8.05,  name: 'Biella' },
  CN: { lat: 44.39, lng: 7.55,  name: 'Cuneo' },
  NO: { lat: 45.45, lng: 8.62,  name: 'Novara' },
  TO: { lat: 45.07, lng: 7.69,  name: 'Torino' },
  VB: { lat: 45.92, lng: 8.55,  name: 'Verbano-Cusio-Ossola' },
  VC: { lat: 45.32, lng: 8.42,  name: 'Vercelli' },
  // ── Valle d'Aosta ───────────────────────────────────────────────────────
  AO: { lat: 45.74, lng: 7.32,  name: "Aosta" },
  // ── Liguria ─────────────────────────────────────────────────────────────
  GE: { lat: 44.41, lng: 8.93,  name: 'Genova' },
  IM: { lat: 43.89, lng: 7.92,  name: 'Imperia' },
  SP: { lat: 44.10, lng: 9.82,  name: 'La Spezia' },
  SV: { lat: 44.31, lng: 8.48,  name: 'Savona' },
  // ── Lombardia ───────────────────────────────────────────────────────────
  BG: { lat: 45.70, lng: 9.67,  name: 'Bergamo' },
  BS: { lat: 45.54, lng: 10.22, name: 'Brescia' },
  CO: { lat: 45.81, lng: 9.09,  name: 'Como' },
  CR: { lat: 45.13, lng: 10.03, name: 'Cremona' },
  LC: { lat: 45.86, lng: 9.40,  name: 'Lecco' },
  LO: { lat: 45.31, lng: 9.50,  name: 'Lodi' },
  MB: { lat: 45.58, lng: 9.27,  name: 'Monza e della Brianza' },
  MI: { lat: 45.47, lng: 9.19,  name: 'Milano' },
  MN: { lat: 45.16, lng: 10.79, name: 'Mantova' },
  PV: { lat: 45.19, lng: 9.16,  name: 'Pavia' },
  SO: { lat: 46.17, lng: 9.87,  name: 'Sondrio' },
  VA: { lat: 45.82, lng: 8.83,  name: 'Varese' },
  // ── Trentino-Alto Adige ─────────────────────────────────────────────────
  BZ: { lat: 46.50, lng: 11.35, name: 'Bolzano / Bozen' },
  TN: { lat: 46.07, lng: 11.12, name: 'Trento' },
  // ── Veneto ──────────────────────────────────────────────────────────────
  BL: { lat: 46.14, lng: 12.22, name: 'Belluno' },
  PD: { lat: 45.41, lng: 11.88, name: 'Padova' },
  RO: { lat: 45.07, lng: 11.79, name: 'Rovigo' },
  TV: { lat: 45.67, lng: 12.24, name: 'Treviso' },
  VE: { lat: 45.44, lng: 12.35, name: 'Venezia' },
  VI: { lat: 45.55, lng: 11.55, name: 'Vicenza' },
  VR: { lat: 45.44, lng: 10.99, name: 'Verona' },
  // ── Friuli-Venezia Giulia ───────────────────────────────────────────────
  GO: { lat: 45.94, lng: 13.62, name: 'Gorizia' },
  PN: { lat: 45.96, lng: 12.66, name: 'Pordenone' },
  TS: { lat: 45.65, lng: 13.78, name: 'Trieste' },
  UD: { lat: 46.07, lng: 13.23, name: 'Udine' },
  // ── Emilia-Romagna ──────────────────────────────────────────────────────
  BO: { lat: 44.49, lng: 11.34, name: 'Bologna' },
  FC: { lat: 44.22, lng: 12.04, name: 'Forlì-Cesena' },
  FE: { lat: 44.84, lng: 11.62, name: 'Ferrara' },
  MO: { lat: 44.65, lng: 10.93, name: 'Modena' },
  PC: { lat: 45.05, lng: 9.70,  name: 'Piacenza' },
  PR: { lat: 44.80, lng: 10.33, name: 'Parma' },
  RA: { lat: 44.42, lng: 12.21, name: 'Ravenna' },
  RE: { lat: 44.70, lng: 10.63, name: 'Reggio Emilia' },
  RN: { lat: 44.06, lng: 12.57, name: 'Rimini' },
  // ── Toscana ─────────────────────────────────────────────────────────────
  AR: { lat: 43.47, lng: 11.88, name: 'Arezzo' },
  FI: { lat: 43.77, lng: 11.25, name: 'Firenze' },
  GR: { lat: 42.76, lng: 11.11, name: 'Grosseto' },
  LI: { lat: 43.54, lng: 10.31, name: 'Livorno' },
  LU: { lat: 43.84, lng: 10.50, name: 'Lucca' },
  MS: { lat: 44.03, lng: 10.14, name: 'Massa-Carrara' },
  PI: { lat: 43.72, lng: 10.40, name: 'Pisa' },
  PO: { lat: 43.88, lng: 11.10, name: 'Prato' },
  PT: { lat: 43.93, lng: 10.91, name: 'Pistoia' },
  SI: { lat: 43.32, lng: 11.33, name: 'Siena' },
  // ── Umbria ──────────────────────────────────────────────────────────────
  PG: { lat: 43.11, lng: 12.39, name: 'Perugia' },
  TR: { lat: 42.56, lng: 12.64, name: 'Terni' },
  // ── Marche ──────────────────────────────────────────────────────────────
  AN: { lat: 43.62, lng: 13.50, name: 'Ancona' },
  AP: { lat: 42.85, lng: 13.57, name: 'Ascoli Piceno' },
  FM: { lat: 43.16, lng: 13.72, name: 'Fermo' },
  MC: { lat: 43.30, lng: 13.45, name: 'Macerata' },
  PU: { lat: 43.91, lng: 12.91, name: 'Pesaro e Urbino' },
  // ── Lazio ───────────────────────────────────────────────────────────────
  FR: { lat: 41.64, lng: 13.35, name: 'Frosinone' },
  LT: { lat: 41.47, lng: 12.90, name: 'Latina' },
  RI: { lat: 42.40, lng: 12.86, name: 'Rieti' },
  RM: { lat: 41.90, lng: 12.49, name: 'Roma' },
  VT: { lat: 42.42, lng: 12.11, name: 'Viterbo' },
  // ── Abruzzo ─────────────────────────────────────────────────────────────
  AQ: { lat: 42.35, lng: 13.40, name: "L'Aquila" },
  CH: { lat: 42.35, lng: 14.17, name: 'Chieti' },
  PE: { lat: 42.46, lng: 14.21, name: 'Pescara' },
  TE: { lat: 42.66, lng: 13.70, name: 'Teramo' },
  // ── Molise ──────────────────────────────────────────────────────────────
  CB: { lat: 41.56, lng: 14.66, name: 'Campobasso' },
  IS: { lat: 41.59, lng: 14.23, name: 'Isernia' },
  // ── Campania ────────────────────────────────────────────────────────────
  AV: { lat: 40.91, lng: 14.79, name: 'Avellino' },
  BN: { lat: 41.13, lng: 14.78, name: 'Benevento' },
  CE: { lat: 41.07, lng: 14.33, name: 'Caserta' },
  NA: { lat: 40.85, lng: 14.27, name: 'Napoli' },
  SA: { lat: 40.68, lng: 14.76, name: 'Salerno' },
  // ── Puglia ──────────────────────────────────────────────────────────────
  BA: { lat: 41.13, lng: 16.87, name: 'Bari' },
  BR: { lat: 40.64, lng: 17.94, name: 'Brindisi' },
  BT: { lat: 41.20, lng: 16.29, name: 'Barletta-Andria-Trani' },
  FG: { lat: 41.46, lng: 15.55, name: 'Foggia' },
  LE: { lat: 40.35, lng: 18.17, name: 'Lecce' },
  TA: { lat: 40.47, lng: 17.24, name: 'Taranto' },
  // ── Basilicata ──────────────────────────────────────────────────────────
  MT: { lat: 40.67, lng: 16.60, name: 'Matera' },
  PZ: { lat: 40.64, lng: 15.80, name: 'Potenza' },
  // ── Calabria ────────────────────────────────────────────────────────────
  CS: { lat: 39.30, lng: 16.25, name: 'Cosenza' },
  CZ: { lat: 38.89, lng: 16.60, name: 'Catanzaro' },
  KR: { lat: 39.08, lng: 17.13, name: 'Crotone' },
  RC: { lat: 38.11, lng: 15.65, name: 'Reggio Calabria' },
  VV: { lat: 38.68, lng: 16.10, name: 'Vibo Valentia' },
  // ── Sicilia ─────────────────────────────────────────────────────────────
  AG: { lat: 37.32, lng: 13.58, name: 'Agrigento' },
  CL: { lat: 37.49, lng: 14.06, name: 'Caltanissetta' },
  CT: { lat: 37.50, lng: 15.09, name: 'Catania' },
  EN: { lat: 37.57, lng: 14.28, name: 'Enna' },
  ME: { lat: 38.19, lng: 15.55, name: 'Messina' },
  PA: { lat: 38.11, lng: 13.35, name: 'Palermo' },
  RG: { lat: 36.93, lng: 14.73, name: 'Ragusa' },
  SR: { lat: 37.08, lng: 15.29, name: 'Siracusa' },
  TP: { lat: 38.02, lng: 12.51, name: 'Trapani' },
  // ── Sardegna ────────────────────────────────────────────────────────────
  CA: { lat: 39.22, lng: 9.11,  name: 'Cagliari' },
  NU: { lat: 40.32, lng: 9.33,  name: 'Nuoro' },
  OR: { lat: 39.90, lng: 8.59,  name: 'Oristano' },
  SS: { lat: 40.73, lng: 8.56,  name: 'Sassari' },
  SU: { lat: 39.37, lng: 8.85,  name: 'Sud Sardegna' },
  // Legacy Sardinian provinces (merged 2016)
  CI: { lat: 39.17, lng: 8.52,  name: 'Carbonia-Iglesias (legacy)' },
  OG: { lat: 39.83, lng: 9.56,  name: 'Ogliastra (legacy)' },
  OT: { lat: 40.92, lng: 9.50,  name: 'Olbia-Tempio (legacy)' },
  VS: { lat: 39.50, lng: 8.93,  name: 'Medio Campidano (legacy)' },
};

/** Return centroid or a sensible Italian-centre fallback if code unknown. */
export function getCentroid(provincia: string): ProvinceCentroid {
  const key = provincia.toUpperCase().trim();
  return PROVINCE_CENTROIDS[key] ?? { lat: 42.5, lng: 12.5, name: provincia };
}
