"""Energivori Delta 2 — DRY-RUN of the contact GATE (mandatory measurement).

Runs geo → Centro-Sud filter → registro decision-maker → personal-email build +
NeverBounce verify → GATE on a bounded slice, and reports the pass-rate + the
distribution of exclusion reasons + email status. NO roof, NO render, NO send —
this is exactly the "measure before wiring the costly path" the spec requires.

The slice makes REAL OpenAPI + Hunter + NeverBounce calls (~40c/in-target
company), so it defaults to a small --limit.

Usage:
    apps/api/.venv/bin/python scripts/energivori_gate_dryrun.py --limit 40
    apps/api/.venv/bin/python scripts/energivori_gate_dryrun.py --limit 60 --strict
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

_API_DIR = Path(__file__).resolve().parent.parent
_ROOT = _API_DIR.parent.parent
load_dotenv(_ROOT / ".env", override=True)
load_dotenv(_API_DIR / ".env", override=True)
sys.path.insert(0, str(_API_DIR))

import httpx  # noqa: E402

from src.core.config import settings  # noqa: E402
from src.services.energivori_contact_gate import resolve_contact_gate  # noqa: E402
from src.services.energivori_import_service import _target_provinces  # noqa: E402
from src.services.energivori_ingest import parse_energivori_xlsx  # noqa: E402
from src.services.openapi_company_service import (  # noqa: E402
    _TIMEOUT,
    OpenApiCreditExhausted,
    fetch_company_enrichment,
    fetch_company_geo,
    fetch_company_stakeholders,
    is_target_province,
)

_DEFAULT_FILE = Path.home() / "Downloads" / "Energivori_2026_Lead_SolarLead.xlsx"


def _pct(n: int, d: int) -> str:
    return f"{(100 * n / d):.0f}%" if d else "—"


async def main() -> None:
    ap = argparse.ArgumentParser(description="Energivori gate dry-run (no roof/render/send)")
    ap.add_argument("--file", type=Path, default=_DEFAULT_FILE)
    ap.add_argument("--limit", type=int, default=40, help="records scanned (geo on each)")
    ap.add_argument("--strict", action="store_true", help="strict gate (only 'valid' passes)")
    args = ap.parse_args()

    for key in ("openapi_it_token", "hunter_api_key", "neverbounce_api_key"):
        if not (getattr(settings, key, "") or "").strip():
            print(f"ERROR: {key.upper()} non impostato — serve per il gate.")
            raise SystemExit(1)
    if not args.file.exists():
        print(f"ERROR: file non trovato: {args.file}")
        raise SystemExit(1)

    acceptall = not args.strict and settings.acceptall_as_medium_confidence
    targets = _target_provinces()
    records = parse_energivori_xlsx(args.file.read_bytes())
    print(
        f"\nIngest {len(records)} P.IVA · target {len(targets)} province Centro-Sud "
        f"· modalità {'RIGIDA' if not acceptall else 'intelligente'}\n"
    )

    scanned = in_target = 0
    passes = 0
    reasons: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    dm_resolved = 0
    examples: list[str] = []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for rec in records[: args.limit]:
            scanned += 1
            geo = await fetch_company_geo(rec.piva, client=client)
            if geo is None or not is_target_province(geo.province, targets):
                continue
            in_target += 1
            enr = await fetch_company_enrichment(rec.piva, client=client)
            if enr is None:
                reasons["no_enrichment"] += 1
                continue
            managers = await fetch_company_stakeholders(rec.piva, client=client)
            gate = await resolve_contact_gate(
                email=enr.email,
                website=enr.website,
                managers=managers,
                client=client,
                acceptall_as_medium=acceptall,
            )
            statuses[gate.email_status] += 1
            if gate.decision_maker_source == "registro":
                dm_resolved += 1
            if gate.passed:
                passes += 1
                if len(examples) < 12:
                    examples.append(
                        f"  PASS {rec.ragione_sociale[:30]:30} {geo.province} "
                        f"→ {gate.email}  [{gate.email_confidence}/{gate.email_source}]"
                    )
            else:
                reasons[gate.excluded_reason or "unknown"] += 1

    print("=== RISULTATO GATE ===")
    print(f"  scansionate            {scanned}")
    print(f"  in Centro-Sud          {in_target}")
    print(f"  decisore da registro   {dm_resolved}  ({_pct(dm_resolved, in_target)})")
    print(f"  PASS (email personale) {passes}  ({_pct(passes, in_target)} del target)")
    print("\n  motivi di scarto:")
    for r, c in reasons.most_common():
        print(f"    {r:22} {c:3}  ({_pct(c, in_target)})")
    print("\n  email_status:")
    for s, c in statuses.most_common():
        print(f"    {s:12} {c:3}")
    if examples:
        print("\n  esempi PASS:")
        print("\n".join(examples))
    print(
        "\n  → Se il PASS-rate è basso, valuta: ammettere 'ufficiotecnico@' (ruolo) "
        "o allentare a media su catch-all, PRIMA di cablare roof/render."
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except OpenApiCreditExhausted:
        print("\nERRORE: account OpenAPI a secco (402). Ricarica su console.openapi.com e riprova.")
        raise SystemExit(2) from None
