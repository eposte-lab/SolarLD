"""Collaudo end-to-end (render + outreach) per Total Trade — IN-PROCESS.

Semina un singolo grafo lead→roof→subject valido a Casavatore (NA),
esegue CreativeAgent (render before/after/video/gif REALI) e poi
OutreachAgent (email REALE, reinstradata via tenant.test_recipient_override
che bypassa il kill-switch outreach_blocked).

NON tocca lo scan L1-L6 (richiede GOOGLE_PLACES_API_KEY, assente in locale —
lo scan reale gira sul worker Railway al go-live).

Uso:
    apps/api/.venv/bin/python scripts/collaudo_total_trade.py
Dopo la verifica, ripulire con --wipe.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Carica PRIMA le chiavi reali (root .env), POI i flag mock (apps/api/.env).
_API_DIR = Path(__file__).resolve().parent.parent
_ROOT = _API_DIR.parent.parent
load_dotenv(_ROOT / ".env", override=True)
load_dotenv(_API_DIR / ".env", override=True)

sys.path.insert(0, str(_API_DIR))

from src.core.supabase_client import get_service_client  # noqa: E402

TENANT_ID = "df08df04-4c90-4613-b21e-80879fc958d1"  # Total Trade
TAG = "collaudo_tt"  # marcatore per cleanup mirato

# Coordinate reali a Casavatore (NA) — capannone zona via Petrarca.
LAT = 40.88665
LNG = 14.29240


def _pii_hash(*parts: str) -> str:
    return hashlib.sha256(("|".join(parts)).encode()).hexdigest()


def seed() -> dict[str, str]:
    sb = get_service_client()
    roof_id = str(uuid.uuid4())
    subject_id = str(uuid.uuid4())
    lead_id = str(uuid.uuid4())
    slug = f"{TAG}-{lead_id[:8]}"

    sb.table("roofs").insert(
        {
            "id": roof_id,
            "tenant_id": TENANT_ID,
            "lat": LAT,
            "lng": LNG,
            "geohash": f"{TAG}_{roof_id[:12]}",
            "address": "Via Petrarca 12, Casavatore (NA)",
            "cap": "80020",
            "comune": "Casavatore",
            "provincia": "NA",
            "area_sqm": 820,
            "estimated_kwp": 95.0,
            "estimated_yearly_kwh": 123500.0,
            "exposure": "sud",
            "pitch_degrees": 8,
            "has_existing_pv": False,
            "data_source": "google_solar",
            "status": "scored",
        }
    ).execute()

    sb.table("subjects").insert(
        {
            "id": subject_id,
            "tenant_id": TENANT_ID,
            "roof_id": roof_id,
            "type": "b2b",
            "business_name": "Collaudo Metalmeccanica Casavatore Srl",
            "vat_number": "IT00000000000",
            "ateco_code": "25.11",
            "ateco_description": "Fabbricazione di strutture metalliche",
            "decision_maker_name": "Mario Rossi",
            "decision_maker_role": "Titolare",
            "decision_maker_email": "collaudo-prospect@example.com",
            "decision_maker_email_verified": True,
            "sede_operativa_address": "Via Petrarca 12, Casavatore (NA)",
            "sede_operativa_city": "Casavatore",
            "sede_operativa_province": "NA",
            "sede_operativa_lat": LAT,
            "sede_operativa_lng": LNG,
            "sede_operativa_source": "atoka",
            "sede_operativa_confidence": "high",
            "pii_hash": _pii_hash(TENANT_ID, "IT00000000000", slug),
        }
    ).execute()

    sb.table("leads").insert(
        {
            "id": lead_id,
            "tenant_id": TENANT_ID,
            "roof_id": roof_id,
            "subject_id": subject_id,
            "public_slug": slug,
            "score": 84,
            "score_tier": "hot",
            "pipeline_status": "ready_to_send",
        }
    ).execute()

    print(f"  seeded roof={roof_id} subject={subject_id} lead={lead_id} slug={slug}")
    return {"roof_id": roof_id, "subject_id": subject_id, "lead_id": lead_id, "slug": slug}


async def run_creative(lead_id: str) -> None:
    from src.agents.creative import CreativeAgent, CreativeInput

    print("  [CreativeAgent] rendering…")
    out = await CreativeAgent().run(
        CreativeInput(tenant_id=TENANT_ID, lead_id=lead_id, force=True)
    )
    print(f"  [CreativeAgent] skipped={out.skipped} reason={out.reason}")
    print(f"    before={out.before_url}")
    print(f"    after ={out.after_url}")
    print(f"    video ={out.video_url}")
    print(f"    gif   ={out.gif_url}")
    print(f"    roi   ={out.roi_data}")


async def run_outreach(lead_id: str) -> None:
    from src.agents.outreach import OutreachAgent, OutreachInput

    print("  [OutreachAgent] sending (rerouted via test_recipient_override)…")
    out = await OutreachAgent().run(
        OutreachInput(tenant_id=TENANT_ID, lead_id=lead_id, force=True)
    )
    print(f"  [OutreachAgent] result={out!r}")


def wipe() -> None:
    sb = get_service_client()
    leads = (
        sb.table("leads").select("id,roof_id,subject_id").eq("tenant_id", TENANT_ID).like("public_slug", f"{TAG}%").execute()
    ).data or []
    for ld in leads:
        sb.table("outreach_sends").delete().eq("lead_id", ld["id"]).execute()
        sb.table("leads").delete().eq("id", ld["id"]).execute()
        if ld.get("subject_id"):
            sb.table("subjects").delete().eq("id", ld["subject_id"]).execute()
        if ld.get("roof_id"):
            sb.table("roofs").delete().eq("id", ld["roof_id"]).execute()
    # difese in più: roof residui per geohash di collaudo
    sb.table("roofs").delete().eq("tenant_id", TENANT_ID).like("geohash", f"{TAG}%").execute()
    print(f"  wiped {len(leads)} collaudo lead(s) + roof/subject/outreach")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true", help="rimuovi i dati di collaudo e esci")
    ap.add_argument("--no-outreach", action="store_true", help="solo render, niente email")
    args = ap.parse_args()

    if args.wipe:
        print("WIPE collaudo data…")
        wipe()
        return

    print("SEED…")
    ids = seed()
    print("CREATIVE…")
    await run_creative(ids["lead_id"])
    if not args.no_outreach:
        print("OUTREACH…")
        await run_outreach(ids["lead_id"])
    print("DONE. lead_id=", ids["lead_id"], "slug=", ids["slug"])


if __name__ == "__main__":
    asyncio.run(main())
