# ATECO Refactor — B2B Precision vero

> ⚠️ **Obsoleto — aprile 2026.** Questo documento descrive il primo
> refactor `b2b_ateco_precision`, che chiamava Solar su tutti i
> candidati Atoka. L'architettura corrente è il **funnel a 4 livelli**
> (`b2b_funnel_v2`): Atoka → Enrichment → AI proxy score → Solar gate
> solo sul top 10-20%. Vedi [`ARCHITECTURE_V2.md`](./ARCHITECTURE_V2.md)
> per la specifica in vigore. Il mode `b2b_ateco_precision` è stato
> rimosso in migration 0035 insieme a `tenant_configs`; nessun writer
> lo emette più. Conservato qui come nota storica.

> **Stato:** obsoleto — superato da `b2b_funnel_v2`.
> **Obiettivo originale:** far partire il discovery B2B da **codici ATECO + filtri azienda** (via Atoka API) invece che da Google Places types.
> **Effort stimato:** 8-12h (revisionato dopo mapping codice esistente).

---

## Perché questo refactor

Il `scan_mode=b2b_precision` attuale usa Google Places Nearby Search con `place_type_whitelist` (es. `restaurant`, `factory`, `car_dealer`). Google Places **non ha i codici ATECO**: ha una sua tassonomia di ~200 tipi generici. Questo significa:

- Il tenant seleziona nel wizard ATECO reali (es. `10.11.00 — Produzione carne`) ma il sistema li mappa grossolanamente a `["food", "meal_takeaway"]` → match impreciso, molti falsi positivi.
- Impossibile filtrare per fatturato/dipendenti in fase di discovery (Places non li espone).
- Per ATECO di servizi professionali (avvocati, commercialisti, consulenti) Google non restituisce quasi nulla.

**La fonte giusta per il B2B italiano è Atoka (SpazioDati)**: il loro endpoint `GET /companies` accetta `ateco`, `locationAreaProvince`, `employeesRange`, `revenueRange` nativamente e restituisce tutte le aziende che matchano con indirizzo HQ + dati completi.

---

## Cosa esiste già (non da toccare)

Mapping del codice attuale ha rivelato parecchia infrastruttura pronta:

| Componente | File | Stato |
|---|---|---|
| `TenantConfig` con `ateco_whitelist`, `min_employees`, `min_revenue_eur`, `atoka_enabled` | `apps/api/src/services/tenant_config_service.py` | ✅ esiste |
| Tabella `tenant_configs` con colonne ATECO + budget | `packages/db/migrations/0013_tenant_configs.sql` | ✅ esiste |
| `AtokaProfile` + `atoka_lookup_by_vat()` | `apps/api/src/services/italian_business_service.py` | ✅ esiste (solo lookup singolo) |
| Wizard onboarding con step ATECO | `apps/dashboard/src/app/(onboarding)/onboarding/page.tsx` | ✅ esiste |
| Route `POST /v1/tenant-config`, `GET /options` | `apps/api/src/routes/tenant_config.py` | ✅ esiste |
| Tabella `subjects` con `ateco_code`, `ateco_description`, `employees` | `packages/db/migrations/0005_subjects.sql` | ✅ esiste |
| `reverse_geocode` Mapbox | `apps/api/src/services/mapbox_service.py` | ✅ esiste |

---

## Cosa va aggiunto/modificato

### 1. Servizio Atoka — nuova funzione di discovery
**File:** `apps/api/src/services/italian_business_service.py`

Aggiungere:
```python
async def atoka_search_by_criteria(
    *,
    ateco_codes: list[str],
    province_code: str | None = None,
    region_code: str | None = None,
    employees_min: int | None = None,
    employees_max: int | None = None,
    revenue_min_eur: int | None = None,
    revenue_max_eur: int | None = None,
    limit: int = 500,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> list[AtokaProfile]:
    """Discovery: restituisce aziende che matchano i criteri.

    Endpoint: GET /companies con parametri Atoka v2.
    Costo stimato: ~€0.003 per azienda restituita (più conveniente di N lookup singole).
    """
```

### 2. Mapbox — forward geocoding
**File:** `apps/api/src/services/mapbox_service.py`

Aggiungere:
```python
async def forward_geocode(
    address: str,
    *,
    country: str = "IT",
    client: httpx.AsyncClient | None = None,
) -> tuple[float, float] | None:
    """Indirizzo → (lat, lng). Ritorna None se non trovato o ambiguo."""
```

### 3. Migration — nuovo valore `scan_mode`
**File nuovo:** `packages/db/migrations/0029_scan_mode_ateco.sql`

```sql
ALTER TABLE tenant_configs DROP CONSTRAINT tenant_configs_scan_mode_check;
ALTER TABLE tenant_configs ADD CONSTRAINT tenant_configs_scan_mode_check
  CHECK (scan_mode IN (
    'b2b_precision',         -- legacy: Google Places
    'b2b_ateco_precision',   -- NEW: Atoka ATECO discovery
    'opportunistic',
    'volume'
  ));
```

### 4. ScanMode enum Python
**File:** `apps/api/src/services/tenant_config_service.py`

Aggiungere `B2B_ATECO_PRECISION = "b2b_ateco_precision"` all'enum `ScanMode`.

### 5. HunterAgent — nuova pipeline
**File:** `apps/api/src/agents/hunter.py`

Aggiungere:
```python
async def _run_b2b_ateco_precision(
    self, *, bbox, payload, config, territory
) -> HunterOutput:
    """Pipeline:
    1. Derive province/region from territory (bbox → reverse-geocode centroide).
    2. atoka_search_by_criteria(ateco=config.ateco_whitelist, ...) → [AtokaProfile]
    3. Dedupe by vat_number vs existing subjects.
    4. For each: forward_geocode(hq_address) → (lat, lng)
    5. Google Solar at that coord → RoofInsight.
    6. apply_technical_filters(config.technical_b2b).
    7. Upsert roofs + subjects (già con ATECO/revenue/employees popolati).
    """
```

Dispatch in `execute()`:
```python
if mode == "b2b_ateco_precision":
    out = await self._run_b2b_ateco_precision(...)
elif mode == "b2b_precision":
    out = await self._run_b2b_precision(...)  # legacy, invariato
```

### 6. Test
**File nuovo:** `apps/api/tests/agents/test_hunter_b2b_ateco.py`

Copertura:
- atoka_search_by_criteria mock → 5 aziende
- forward_geocode mock → coord
- Solar mock → insight valido su 4/5
- Filter blocks 1/4
- Output: 3 roofs discovered + 3 subjects creati

### 7. Rewrite del wizard onboarding per il nuovo mode
**File:** `apps/dashboard/src/app/(onboarding)/onboarding/page.tsx`

Aggiungere nel selector mode il nuovo valore. Probabilmente già compatibile perché il wizard salva `ateco_whitelist` — va solo mappato lo scan_mode default quando il tenant sceglie "solo B2B".

---

## Ordine di esecuzione

1. Spec doc (questo file) — **fatto**
2. `atoka_search_by_criteria()` in servizio
3. `forward_geocode()` in Mapbox service
4. Migration `0029`
5. `ScanMode` enum + dataclass config
6. `_run_b2b_ateco_precision()` in HunterAgent
7. Dispatch in `execute()`
8. Test unit
9. Aggiornamento `docs/PRICING.md` con separazione Volume vs Precision
10. Commit + push

## Rollback safety

- Il mode legacy `b2b_precision` (Google Places) resta funzionante — coesistenza senza rottura.
- Nessuna tabella modificata strutturalmente: solo enum check relax.
- Tenant esistenti continuano a usare il vecchio fino a che non cambiano `scan_mode` dal wizard.
