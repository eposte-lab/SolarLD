"""Idempotent seed for a staging smoke-test tenant.

Creates (or refreshes) a minimal but realistic tenant graph:

    tenant → territory (80100 Napoli) → 3 roofs → 3 subjects → 3 leads

Run:
    .venv/bin/python scripts/seed_test_tenant.py
    .venv/bin/python scripts/seed_test_tenant.py --reset   # wipe child rows first
    .venv/bin/python scripts/seed_test_tenant.py --tenant-vat VAT-CUSTOM

Requires `SUPABASE_SERVICE_ROLE_KEY` + `NEXT_PUBLIC_SUPABASE_URL` in env,
since we bypass RLS via the service-role key.

Idempotency is by `tenants.vat_number` (UNIQUE) — re-running updates the
same row rather than duplicating. `--reset` truncates the tenant's child
rows (events / leads / campaigns / subjects / roofs) before re-seeding,
which accelerates smoke-test iteration without recreating the tenant.

Leads created here are intentionally pre-pipeline (status='new', score=0)
so the smoke test exercises the scoring → creative → outreach flow end-
to-end rather than landing in a pre-scored state.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

# Allow `python scripts/seed_test_tenant.py` from apps/api/ without install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.supabase_client import get_service_client  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures — kept verbose on purpose so a reviewer can eyeball what's seeded.
# ---------------------------------------------------------------------------

DEFAULT_VAT = "IT12345678901"
DEFAULT_BUSINESS_NAME = "Solare Napoli Test"
DEFAULT_EMAIL = "admin+test@solarlead.local"

TERRITORY = {
    "type": "cap",
    "code": "80100",
    "name": "Napoli Centro",
    "priority": 5,
}

# Three realistic Napoli coordinates so the seed doesn't all geohash to
# the same cluster — the territory scan would otherwise dedupe them.
ROOFS: list[dict[str, Any]] = [
    {
        "lat": 40.8518,
        "lng": 14.2681,
        "address": "Via Toledo 256, Napoli",
        "cap": "80132",
        "comune": "Napoli",
        "provincia": "NA",
        "area_sqm": 180.0,
        "estimated_kwp": 18.0,
        "estimated_yearly_kwh": 22000.0,
        "exposure": "S",
        "pitch_degrees": 18.0,
        "shading_score": 0.15,
        "classification": "business",
    },
    {
        "lat": 40.8359,
        "lng": 14.2488,
        "address": "Via Chiaia 45, Napoli",
        "cap": "80121",
        "comune": "Napoli",
        "provincia": "NA",
        "area_sqm": 95.0,
        "estimated_kwp": 9.5,
        "estimated_yearly_kwh": 11500.0,
        "exposure": "SE",
        "pitch_degrees": 22.0,
        "shading_score": 0.20,
        "classification": "residential",
    },
    {
        "lat": 40.8632,
        "lng": 14.2906,
        "address": "Piazza Garibaldi 12, Napoli",
        "cap": "80142",
        "comune": "Napoli",
        "provincia": "NA",
        "area_sqm": 240.0,
        "estimated_kwp": 24.0,
        "estimated_yearly_kwh": 29500.0,
        "exposure": "S",
        "pitch_degrees": 15.0,
        "shading_score": 0.10,
        "classification": "business",
    },
]

SUBJECTS: list[dict[str, Any]] = [
    {
        "type": "business",
        "business_name": "Pizzeria Da Michele SRL",
        "vat_number": "IT01234567001",
        "decision_maker_name": "Mario Rossi",
        "decision_maker_role": "CEO",
        "decision_maker_email": "mario.rossi+test@solarlead.local",
        "decision_maker_email_verified": True,
    },
    {
        "type": "residential",
        "owner_first_name": "Giulia",
        "owner_last_name": "Esposito",
        "postal_address_line1": "Via Chiaia 45",
        "postal_cap": "80121",
        "postal_city": "Napoli",
        "postal_province": "NA",
    },
    {
        "type": "business",
        "business_name": "Bar Centrale SNC",
        "vat_number": "IT01234567002",
        "decision_maker_name": "Luca Bianchi",
        "decision_maker_role": "Titolare",
        "decision_maker_email": "luca.bianchi+test@solarlead.local",
        "decision_maker_email_verified": True,
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _geohash(lat: float, lng: float, precision: int = 9) -> str:
    """Tiny geohash impl — same shape as the real HunterAgent output.

    We don't import python-geohash here to keep the script dependency-free;
    a 9-char geohash is precise enough to disambiguate our 3 fixtures.
    """
    _base32 = "0123456789bcdefghjkmnpqrstuvwxyz"
    lat_range = [-90.0, 90.0]
    lng_range = [-180.0, 180.0]
    bits = []
    even = True
    while len(bits) < precision * 5:
        if even:
            mid = sum(lng_range) / 2
            if lng >= mid:
                bits.append(1)
                lng_range[0] = mid
            else:
                bits.append(0)
                lng_range[1] = mid
        else:
            mid = sum(lat_range) / 2
            if lat >= mid:
                bits.append(1)
                lat_range[0] = mid
            else:
                bits.append(0)
                lat_range[1] = mid
        even = not even
    out = ""
    for i in range(0, len(bits), 5):
        v = int("".join(str(b) for b in bits[i : i + 5]), 2)
        out += _base32[v]
    return out


def _pii_hash(subject: dict[str, Any]) -> str:
    """SHA256 over normalized identity — matches IdentityAgent's rule."""
    if subject["type"] == "business":
        raw = f"{subject.get('business_name', '')}|{subject.get('vat_number', '')}"
    else:
        raw = (
            f"{subject.get('owner_first_name', '')}|"
            f"{subject.get('owner_last_name', '')}|"
            f"{subject.get('postal_address_line1', '')}"
        )
    return hashlib.sha256(raw.lower().strip().encode()).hexdigest()


def _public_slug(seed: str) -> str:
    """Short URL-safe token for leads.public_slug."""
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Seed operations
# ---------------------------------------------------------------------------


def upsert_tenant(sb, vat: str, business_name: str, email: str) -> str:
    row = {
        "business_name": business_name,
        "vat_number": vat,
        "contact_email": email,
        "email_from_domain": "test.solarlead.local",
        "email_from_name": business_name,
        "tier": "pro",  # unlocks WhatsApp + realtime so smoke covers tier-gated UI
        "status": "active",
        "settings": {
            "_seed": True,
            "_seed_note": "Created by scripts/seed_test_tenant.py — safe to delete.",
        },
    }
    res = sb.table("tenants").upsert(row, on_conflict="vat_number").execute()
    tenant_id = res.data[0]["id"]
    print(f"  ✓ tenant {tenant_id} ({business_name})")
    return tenant_id


def upsert_territory(sb, tenant_id: str) -> str:
    row = {"tenant_id": tenant_id, **TERRITORY}
    res = (
        sb.table("territories")
        .upsert(row, on_conflict="tenant_id,type,code")
        .execute()
    )
    territory_id = res.data[0]["id"]
    print(f"  ✓ territory {territory_id} (cap={TERRITORY['code']})")
    return territory_id


def upsert_roofs(sb, tenant_id: str, territory_id: str) -> list[str]:
    ids: list[str] = []
    for i, fixture in enumerate(ROOFS):
        row = {
            **fixture,
            "tenant_id": tenant_id,
            "territory_id": territory_id,
            "geohash": _geohash(fixture["lat"], fixture["lng"]),
            "data_source": "seed_script",
            "status": "qualified",
            "raw_data": {"_seed": True, "_index": i},
        }
        res = (
            sb.table("roofs").upsert(row, on_conflict="tenant_id,geohash").execute()
        )
        ids.append(res.data[0]["id"])
    print(f"  ✓ {len(ids)} roofs")
    return ids


def upsert_subjects(sb, tenant_id: str, roof_ids: list[str]) -> list[str]:
    ids: list[str] = []
    for roof_id, fixture in zip(roof_ids, SUBJECTS, strict=True):
        row = {
            **fixture,
            "tenant_id": tenant_id,
            "roof_id": roof_id,
            "pii_hash": _pii_hash(fixture),
            "enrichment_completed_at": "2026-01-01T00:00:00Z",
        }
        res = (
            sb.table("subjects")
            .upsert(row, on_conflict="tenant_id,roof_id")
            .execute()
        )
        ids.append(res.data[0]["id"])
    print(f"  ✓ {len(ids)} subjects")
    return ids


def upsert_leads(
    sb, tenant_id: str, roof_ids: list[str], subject_ids: list[str]
) -> list[str]:
    ids: list[str] = []
    for i, (roof_id, subject_id) in enumerate(
        zip(roof_ids, subject_ids, strict=True)
    ):
        slug = _public_slug(f"{tenant_id}:{roof_id}:{i}")
        row = {
            "tenant_id": tenant_id,
            "roof_id": roof_id,
            "subject_id": subject_id,
            "public_slug": slug,
            "score": 0,
            "score_tier": "cold",
            "pipeline_status": "new",
        }
        # public_slug is UNIQUE — idempotent re-seed re-uses the same slug.
        res = (
            sb.table("leads").upsert(row, on_conflict="public_slug").execute()
        )
        ids.append(res.data[0]["id"])
    print(f"  ✓ {len(ids)} leads")
    return ids


def reset_tenant_data(sb, tenant_id: str) -> None:
    """Truncate the tenant's dependent rows. Leaves tenant + territory."""
    # ORDER MATTERS: FK cascades would clean up, but being explicit makes
    # the intent clear and surfaces any new child tables the author forgot.
    for table in ("events", "campaigns", "leads", "subjects", "roofs"):
        sb.table(table).delete().eq("tenant_id", tenant_id).execute()
    print(f"  ✓ reset child rows for tenant {tenant_id}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant-vat", default=DEFAULT_VAT)
    ap.add_argument("--business-name", default=DEFAULT_BUSINESS_NAME)
    ap.add_argument("--email", default=DEFAULT_EMAIL)
    ap.add_argument(
        "--reset",
        action="store_true",
        help="Delete this tenant's leads/campaigns/events/subjects/roofs "
        "before re-seeding. Safe; tenant + territory are preserved.",
    )
    args = ap.parse_args()

    sb = get_service_client()

    print(f"Seeding tenant vat={args.tenant_vat}…")
    tenant_id = upsert_tenant(sb, args.tenant_vat, args.business_name, args.email)

    if args.reset:
        reset_tenant_data(sb, tenant_id)

    territory_id = upsert_territory(sb, tenant_id)
    roof_ids = upsert_roofs(sb, tenant_id, territory_id)
    subject_ids = upsert_subjects(sb, tenant_id, roof_ids)
    lead_ids = upsert_leads(sb, tenant_id, roof_ids, subject_ids)

    print(f"\nDone. Tenant {tenant_id} has {len(lead_ids)} seed leads ready.")
    print("Next: open dashboard, navigate to Leads, run smoke protocol.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
