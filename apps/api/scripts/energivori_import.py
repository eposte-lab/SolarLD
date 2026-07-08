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

from src.core.config import settings  # noqa: E402
from src.services.energivori_import_service import run_import  # noqa: E402
from src.services.energivori_ingest import parse_energivori_xlsx  # noqa: E402

_DEFAULT_FILE = Path.home() / "Downloads" / "Energivori_2026_Lead_SolarLead.xlsx"


def _eur(cents: int) -> str:
    return f"€{cents / 100:,.2f}"


async def main() -> None:
    ap = argparse.ArgumentParser(description="Energivori dry-run (no DB writes)")
    ap.add_argument("--file", type=Path, default=_DEFAULT_FILE, help="CSEA xlsx path")
    ap.add_argument("--sheet", default=None, help="worksheet name (default: first)")
    ap.add_argument("--limit", type=int, default=25, help="VATs to enrich (cost!)")
    args = ap.parse_args()

    if not (settings.openapi_it_token or "").strip():
        print("ERROR: OPENAPI_IT_TOKEN non impostato (root .env o env) — impossibile enrichire.")
        raise SystemExit(1)
    if not args.file.exists():
        print(f"ERROR: file non trovato: {args.file}")
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


if __name__ == "__main__":
    asyncio.run(main())
