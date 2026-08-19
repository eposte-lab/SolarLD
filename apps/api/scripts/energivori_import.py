"""Energivori channel — DRY-RUN a slice of the CSEA list through OpenAPI.

Reads the CSEA "energivori" workbook, enriches a bounded slice via
company.openapi.com (geo filter → IT-marketing → productive-site selection),
and PRINTS the numbers + a cost estimate. Writes NOTHING to the DB — it is the
"look before you spend" step before the full batch + the DB-write wiring.

The slice still makes REAL OpenAPI calls (cheap: geo ~5c on each sampled VAT,
enrichment ~10c only on the in-target subset), so it defaults to a small
--limit. The full-batch projection at the end extrapolates from the sample's
Campania hit-rate — the actual spend depends on your OpenAPI.it plan.

Usage:
    apps/api/.venv/bin/python scripts/energivori_import.py --limit 25
    apps/api/.venv/bin/python scripts/energivori_import.py \\
        --file ~/Downloads/Energivori_2026_Lead_SolarLead.xlsx --limit 50
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

# Real keys first (root .env), then any local flags (apps/api/.env).
_API_DIR = Path(__file__).resolve().parent.parent
_ROOT = _API_DIR.parent.parent
load_dotenv(_ROOT / ".env", override=True)
load_dotenv(_API_DIR / ".env", override=True)
sys.path.insert(0, str(_API_DIR))

import httpx  # noqa: E402

from src.core.config import settings  # noqa: E402
from src.services.energivori_import_service import (  # noqa: E402
    prepare_items,
    run_import,
)
from src.services.energivori_ingest import parse_energivori_xlsx  # noqa: E402
from src.services.openapi_company_service import (  # noqa: E402
    _TIMEOUT,
    TARGET_PROVINCES,
    OpenApiCreditExhausted,
)
from src.services.prospector_service import (  # noqa: E402
    create_prospect_list_from_openapi,
)

_DEFAULT_FILE = Path.home() / "Downloads" / "Energivori_2026_Lead_SolarLead.xlsx"
_TOTAL_TRADE = "df08df04-4c90-4613-b21e-80879fc958d1"


def _eur(cents: int) -> str:
    return f"€{cents / 100:,.2f}"


async def main() -> None:
    ap = argparse.ArgumentParser(description="Energivori import (dry-run by default)")
    ap.add_argument("--file", type=Path, default=_DEFAULT_FILE, help="CSEA xlsx path")
    ap.add_argument("--sheet", default=None, help="worksheet name (default: first)")
    ap.add_argument("--limit", type=int, default=25, help="VATs to enrich (cost!)")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="WRITE a prospect_list from the in-target prospects (geocodes + costs)",
    )
    ap.add_argument("--tenant", default=_TOTAL_TRADE, help="tenant_id for --apply")
    ap.add_argument("--list-name", default=None, help="prospect_list name for --apply")
    ap.add_argument(
        "--force",
        action="store_true",
        help="with --apply: create even if a same-name openapi_it list already exists",
    )
    args = ap.parse_args()

    if not (settings.openapi_it_token or "").strip():
        print("ERROR: OPENAPI_IT_TOKEN non impostato (root .env o env) — impossibile enrichire.")
        raise SystemExit(1)
    if args.apply and not (settings.mapbox_access_token or "").strip():
        print("ERROR: --apply richiede MAPBOX_ACCESS_TOKEN (geocodifica sede) — non impostato.")
        raise SystemExit(1)
    if not args.file.exists():
        print(f"ERROR: file non trovato: {args.file}")
        raise SystemExit(1)

    # Idempotency guard runs BEFORE the (paid) enrichment so a duplicate --apply
    # never re-spends. A stable default name makes the same-name check meaningful.
    list_name = args.list_name or "Energivori Campania (OpenAPI)"
    if args.apply and not args.force:
        from src.core.supabase_client import get_service_client

        dup = (
            get_service_client()
            .table("prospect_lists")
            .select("id")
            .eq("tenant_id", args.tenant)
            .eq("source", "openapi_it")
            .eq("name", list_name)
            .limit(1)
            .execute()
        )
        if dup.data:
            print(
                f"ERROR: esiste già una lista openapi_it '{list_name}' "
                f"(id={dup.data[0]['id']}). Usa --force o --list-name per crearne un'altra."
            )
            raise SystemExit(1)

    records = parse_energivori_xlsx(args.file.read_bytes(), sheet=args.sheet)
    total = len(records)
    print(f"\nIngest: {total} aziende con P.IVA valida da {args.file.name}")
    if not total:
        return

    n = min(args.limit, total)
    print(f"Dry-run su {n} (geo su tutte, enrichment solo sul sottoinsieme Campania)…\n")
    s = await run_import(records, limit=n)

    hit_rate = (s.in_target / s.total) if s.total else 0.0
    print("=== CAMPIONE ===")
    print(f"  sampled            {s.total}")
    print(f"  in Campania        {s.in_target}  ({hit_rate:.0%})")
    print(f"  enriched (ateco)   {s.enriched}")
    print(f"  render-site HIGH   {s.render_high}  (sede produttiva in-regione, pronta al render)")
    print(f"  con email/PEC      {s.with_email}")
    print(f"  costo campione     {_eur(s.est_cost_cents)} (stima)")

    # Full-batch projection: geo on ALL, enrichment only on the Campania subset.
    from src.services.energivori_import_service import (
        _COST_ENRICH_CENTS,
        _COST_GEO_CENTS,
    )

    proj_enrich = round(hit_rate * total)
    proj_cents = total * _COST_GEO_CENTS + proj_enrich * _COST_ENRICH_CENTS
    print("\n=== PROIEZIONE BATCH PIENO ===")
    print(f"  {total} P.IVA → geo su tutte + enrichment su ~{proj_enrich} (Campania stimate)")
    print(f"  costo stimato      {_eur(proj_cents)}  (dipende dal piano OpenAPI.it)")

    print("\n=== ESEMPI (in Campania) ===")
    for p in (s.prospects or [])[:12]:
        contact = p.email or p.pec or p.phone or "—"
        print(
            f"  {p.ragione_sociale[:34]:34} {p.province or '??'} "
            f"ateco={p.ateco_code or '—':>5} "
            f"render[{p.render_confidence:4}]={(p.render_address or '—')[:44]}  {contact}"
        )

    if not args.apply:
        print("\n(dry-run — nessuna scrittura. Aggiungi --apply per creare la lista.)")
        return

    # --- APPLY: geocode the render sites + write a prospect_list ------------
    prospects = s.prospects or []
    if not prospects:
        print("\nNessun prospect in Campania da scrivere.")
        return
    print(f"\n=== APPLY: geocodifica {len(prospects)} sedi + scrittura lista… ===")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        prep = await prepare_items(prospects, client=client)
    list_row = create_prospect_list_from_openapi(
        args.tenant,
        list_name,
        prep.items,
        description="Import energivori CSEA via OpenAPI.it",
        search_filter={"channel": "openapi_it", "provinces": sorted(TARGET_PROVINCES)},
    )
    print(f"  lista creata       id={list_row['id']}  name={list_name!r}")
    print(f"  item scritti       {list_row.get('item_count', len(prep.items))}")
    print(f"  geocodificati      {prep.geocoded}  (con coordinate → validabili)")
    print(f"  senza coordinate   {prep.skipped_geocode}  (saranno 'skipped' in convalida)")
    if prep.gate_dropped:
        print(f"  scartati dal gate  {prep.gate_dropped}  (solo generica → niente roof/render)")
    print("\nProssimo passo: convalida la lista dalla dashboard /scoperta (o via task).")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except OpenApiCreditExhausted:
        print(
            "\nERRORE: account OpenAPI a secco (402). Ricarica il credito su "
            "console.openapi.com e riprova — nessuna lista scritta."
        )
        raise SystemExit(2) from None
