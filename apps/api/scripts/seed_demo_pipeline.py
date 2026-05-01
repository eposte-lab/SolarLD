"""Comprehensive demo pipeline seeder — QA / presales validation.

Populates (or refreshes) a tenant with a complete, realistic graph of
leads, a preventivo, and a full GSE practice so every screen in the
dashboard has something to show during a QA run or presales demo.

What gets created
─────────────────
  • 1 tenant  "Demo Solar Srl" (or attaches to an existing one via --tenant-id)
  • 1 territory  CAP 20121 Milano
  • 10 leads across every lifecycle status:
       new · sent · delivered · opened · clicked · engaged ·
       appointment · closed_won+contract_signed · closed_lost · cold/new
  • 1 outreach_send row (for the "sent" lead)
  • 1 lead_quote / preventivo (for the contract_signed lead)
  • 1 practice with 9 practice_documents spanning every template code
  • 3 practice_deadlines: 1 overdue · 1 imminent (≤7 gg) · 1 far
  • practice_events for the champion lead's full timeline

Company fixtures are drawn from the 10 entries in demo_mock_enrichment
so phone-source chips, ATECO copy, and revenue tiers all look real.

Run
───
    # from apps/api/ directory:
    .venv/bin/python scripts/seed_demo_pipeline.py

    # attach to an existing tenant:
    .venv/bin/python scripts/seed_demo_pipeline.py --tenant-id <uuid>

    # wipe child rows and re-seed fresh:
    .venv/bin/python scripts/seed_demo_pipeline.py --reset

    # also enqueue PDF render tasks (requires arq worker + WeasyPrint):
    .venv/bin/python scripts/seed_demo_pipeline.py --reset --full-render

Requires
────────
    SUPABASE_SERVICE_ROLE_KEY + NEXT_PUBLIC_SUPABASE_URL in env (or .env).
    Bypasses RLS via the service-role key — never run against prod.

Idempotency
───────────
    Without --reset: upserts on every UNIQUE constraint.  Re-running the
    same command twice is safe and produces the same IDs.
    With --reset: deletes all child rows for the tenant first, then seeds
    fresh.  The tenant row itself is preserved.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Allow `python scripts/seed_demo_pipeline.py` from apps/api/ without install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.supabase_client import get_service_client  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEMO_TENANT_VAT = "IT98765432101"
DEMO_BUSINESS_NAME = "Demo Solar Srl"
DEMO_CONTACT_EMAIL = "demo@agenda-pro.it"

# Template codes for the practice documents.
# Drawn from practice_deadlines_service.DEADLINE_RULES + sprint plan.
TEMPLATE_CODES = [
    "dm_37_08",
    "comunicazione_comune",
    "modello_unico_p1",
    "modello_unico_p2",
    "schema_unifilare",
    "attestazione_titolo",
    "tica_areti",
    "transizione_50_ex_ante",
    "transizione_50_ex_post",
]

# Deadline kinds + due offsets for the 3 seed deadlines.
# deadline_kind values must match DEADLINE_RULES.kind in practice_deadlines_service.py.
DEMO_DEADLINES = [
    {
        "kind": "comune_acceptance_30d",
        "days_offset": -5,   # 5 days AGO → overdue
        "status": "overdue",
        "title": "Silenzio-assenso Comune",
        "reference": "DPR 380/2001 art. 6 — 30 gg dalla comunicazione",
    },
    {
        "kind": "tica_response_60d",
        "days_offset": +4,   # 4 days AHEAD → imminent
        "status": "open",
        "title": "Risposta TICA distributore",
        "reference": "ARERA 109/2021 art. 7 — 60 gg dalla domanda",
    },
    {
        "kind": "transizione_50_ex_post_60d",
        "days_offset": +45,  # 45 days AHEAD → far
        "status": "open",
        "title": "Comunicazione ex-post Transizione 5.0",
        "reference": "D.L. 19/2024 art. 38 — 60 gg da entrata in esercizio",
    },
]

# ---------------------------------------------------------------------------
# Fixtures — 10 companies (one per lead state)
# All are seeded in demo_mock_enrichment (migrations 0079 + 0089).
# ---------------------------------------------------------------------------

# (vat_number, legal_name, comune, provincia, cap, lat, lng,
#  decision_maker_name, decision_maker_role, email,
#  ateco_description, yearly_revenue_cents, employees)
COMPANIES = [
    {
        "vat": "01845680974",
        "name": "Logistica Toscana Srl",
        "comune": "Prato",
        "provincia": "PO",
        "cap": "59100",
        "lat": 43.8677,
        "lng": 11.0941,
        "address": "Via dell'Artigianato 12, 59100 Prato PO",
        "decision_maker_name": "Davide Bini",
        "decision_maker_role": "Responsabile Operativo",
        "decision_maker_email": "dbini+demo@agenda-pro.it",
        "ateco": "Trasporto di merci su strada e servizi di trasloco",
        "revenue_cents": 600_000_000,
        "employees": 62,
        "area_sqm": 2200.0,
        "kwp": 180.0,
        "kwh": 216_000.0,
        "lead_state": "new",
        "score": 45,
        "tier": "warm",
    },
    {
        "vat": "09881610019",
        "name": "Multilog Spa",
        "comune": "Caivano",
        "provincia": "NA",
        "cap": "80023",
        "lat": 40.9526,
        "lng": 14.3038,
        "address": "Agglomerato ASI Pascarola, 80023 Caivano NA",
        "decision_maker_name": "Andrea Esposito",
        "decision_maker_role": "CEO",
        "decision_maker_email": "aesposito+demo@agenda-pro.it",
        "ateco": "Trasporto di merci su strada",
        "revenue_cents": 3_750_000_000,
        "employees": 48,
        "area_sqm": 3500.0,
        "kwp": 280.0,
        "kwh": 336_000.0,
        "lead_state": "sent",
        "score": 72,
        "tier": "hot",
    },
    {
        "vat": "02134560988",
        "name": "Officine Meccaniche Lombarde Srl",
        "comune": "Roncadelle",
        "provincia": "BS",
        "cap": "25030",
        "lat": 45.5178,
        "lng": 10.1567,
        "address": "Via Industriale 88, 25030 Roncadelle BS",
        "decision_maker_name": "Marco Bertoli",
        "decision_maker_role": "Direttore Tecnico",
        "decision_maker_email": "mbertoli+demo@agenda-pro.it",
        "ateco": "Fabbricazione di strutture metalliche e di parti di strutture",
        "revenue_cents": 420_000_000,
        "employees": 38,
        "area_sqm": 1800.0,
        "kwp": 145.0,
        "kwh": 174_000.0,
        "lead_state": "delivered",
        "score": 58,
        "tier": "warm",
    },
    {
        "vat": "01956780360",
        "name": "Ceramiche Emiliane Spa",
        "comune": "Sassuolo",
        "provincia": "MO",
        "cap": "41049",
        "lat": 44.5487,
        "lng": 10.7863,
        "address": "Via Radici in Piano 112, 41049 Sassuolo MO",
        "decision_maker_name": "Silvia Cavazzoni",
        "decision_maker_role": "CFO",
        "decision_maker_email": "scavazzoni+demo@agenda-pro.it",
        "ateco": "Fabbricazione di piastrelle e lastre in ceramica",
        "revenue_cents": 1_800_000_000,
        "employees": 145,
        "area_sqm": 8000.0,
        "kwp": 640.0,
        "kwh": 768_000.0,
        "lead_state": "opened",
        "score": 65,
        "tier": "warm",
    },
    {
        "vat": "04356780016",
        "name": "Cartotecnica Piemontese Srl",
        "comune": "Torino",
        "provincia": "TO",
        "cap": "10151",
        "lat": 45.0812,
        "lng": 7.6234,
        "address": "Via Pianezza 231, 10151 Torino TO",
        "decision_maker_name": "Gianni Ferreri",
        "decision_maker_role": "Titolare",
        "decision_maker_email": "gferreri+demo@agenda-pro.it",
        "ateco": "Fabbricazione di imballaggi in carta e cartone",
        "revenue_cents": 310_000_000,
        "employees": 29,
        "area_sqm": 1200.0,
        "kwp": 96.0,
        "kwh": 115_200.0,
        "lead_state": "clicked",
        "score": 78,
        "tier": "hot",
    },
    {
        "vat": "04834567891",
        "name": "Costruzioni Edili Sicule Srl",
        "comune": "Catania",
        "provincia": "CT",
        "cap": "95121",
        "lat": 37.5079,
        "lng": 15.0830,
        "address": "Via Etnea 312, 95121 Catania CT",
        "decision_maker_name": "Rosario Grasso",
        "decision_maker_role": "Amministratore Unico",
        "decision_maker_email": "rgrasso+demo@agenda-pro.it",
        "ateco": "Costruzione di edifici residenziali e non residenziali",
        "revenue_cents": 280_000_000,
        "employees": 23,
        "area_sqm": 900.0,
        "kwp": 72.0,
        "kwh": 86_400.0,
        "lead_state": "engaged",
        "score": 82,
        "tier": "hot",
        "feedback": "qualified",
    },
    {
        "vat": "03245678908",
        "name": "Frigoriferi Industriali Veneti Srl",
        "comune": "Rubano",
        "provincia": "PD",
        "cap": "35030",
        "lat": 45.4062,
        "lng": 11.8289,
        "address": "Via delle Industrie 44, 35030 Sarmeola di Rubano PD",
        "decision_maker_name": "Elena Zampieri",
        "decision_maker_role": "CEO",
        "decision_maker_email": "ezampieri+demo@agenda-pro.it",
        "ateco": "Fabbricazione di apparecchiature di refrigerazione e ventilazione",
        "revenue_cents": 950_000_000,
        "employees": 78,
        "area_sqm": 4500.0,
        "kwp": 360.0,
        "kwh": 432_000.0,
        "lead_state": "appointment",
        "score": 88,
        "tier": "hot",
    },
    {
        "vat": "06578901218",
        "name": "Tessile Campana Srl",
        "comune": "Casalnuovo",
        "provincia": "NA",
        "cap": "80013",
        "lat": 40.9212,
        "lng": 14.3615,
        "address": "Interporto Sud Europa, Via Argine 425, 80013 Casalnuovo NA",
        "decision_maker_name": "Carmela Napolitano",
        "decision_maker_role": "Titolare",
        "decision_maker_email": "cnapolitano+demo@agenda-pro.it",
        "ateco": "Fabbricazione di altri prodotti tessili",
        "revenue_cents": 570_000_000,
        "employees": 51,
        "area_sqm": 2800.0,
        "kwp": 224.0,
        "kwh": 268_800.0,
        "lead_state": "closed_won",
        "score": 91,
        "tier": "hot",
        "feedback": "contract_signed",
        "contract_value_cents": 52_400_00,   # €52.400
        "_is_champion": True,                # this lead gets quote + practice
    },
    {
        "vat": "01534568096",
        "name": "Imballaggi Liguri Srl",
        "comune": "Genova",
        "provincia": "GE",
        "cap": "16162",
        "lat": 44.4520,
        "lng": 8.8790,
        "address": "Via Chiaravagna 37, 16162 Genova GE",
        "decision_maker_name": "Pietro Malatesta",
        "decision_maker_role": "Direttore Commerciale",
        "decision_maker_email": "pmalatesta+demo@agenda-pro.it",
        "ateco": "Fabbricazione di imballaggi in legno",
        "revenue_cents": 160_000_000,
        "employees": 14,
        "area_sqm": 600.0,
        "kwp": 48.0,
        "kwh": 57_600.0,
        "lead_state": "closed_lost",
        "score": 55,
        "tier": "warm",
        "feedback": "not_interested",
    },
    {
        "vat": "11456781009",
        "name": "Alimentari del Lazio Srl",
        "comune": "Roma",
        "provincia": "RM",
        "cap": "00143",
        "lat": 41.8068,
        "lng": 12.5002,
        "address": "Via Laurentina 819, 00143 Roma RM",
        "decision_maker_name": "Fabrizio Borghese",
        "decision_maker_role": "Responsabile Acquisti",
        "decision_maker_email": "fborghese+demo@agenda-pro.it",
        "ateco": "Produzione di paste alimentari, di cuscus e prodotti farinacei simili",
        "revenue_cents": 1_120_000_000,
        "employees": 92,
        "area_sqm": 5200.0,
        "kwp": 416.0,
        "kwh": 499_200.0,
        "lead_state": "new",
        "score": 18,
        "tier": "cold",
    },
]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _geohash(lat: float, lng: float, precision: int = 9) -> str:
    """Pure-Python geohash — no external dep needed in a seed script."""
    _base32 = "0123456789bcdefghjkmnpqrstuvwxyz"
    lat_range = [-90.0, 90.0]
    lng_range = [-180.0, 180.0]
    bits: list[int] = []
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


def _pii_hash(business_name: str, vat: str) -> str:
    raw = f"{business_name.lower().strip()}|{vat.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _slug(tenant_id: str, vat: str) -> str:
    return hashlib.sha256(f"demo:{tenant_id}:{vat}".encode()).hexdigest()[:16]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _days_ago(n: int) -> str:
    return _iso(_now() - timedelta(days=n))


def _days_from_now(n: int) -> str:
    return _iso(_now() + timedelta(days=n))


# ---------------------------------------------------------------------------
# Seed operations
# ---------------------------------------------------------------------------


def _get_or_create_tenant(sb: Any, tenant_id: str | None) -> str:
    if tenant_id:
        res = sb.table("tenants").select("id, business_name").eq("id", tenant_id).limit(1).execute()
        if not res.data:
            print(f"  ✗ tenant {tenant_id} not found", file=sys.stderr)
            sys.exit(1)
        print(f"  → using existing tenant {res.data[0]['id']} ({res.data[0]['business_name']})")
        return res.data[0]["id"]

    row = {
        "business_name": DEMO_BUSINESS_NAME,
        "vat_number": DEMO_TENANT_VAT,
        "contact_email": DEMO_CONTACT_EMAIL,
        "email_from_domain": "agenda-pro.it",
        "email_from_name": DEMO_BUSINESS_NAME,
        "tier": "pro",
        "status": "active",
        "is_demo": True,
        "demo_pipeline_test_remaining": 999,
        # Sprint 1 legal fields (0082)
        "codice_fiscale": "98765432101",
        "numero_cciaa": "MI-9876543",
        "responsabile_tecnico_nome": "Giovanni",
        "responsabile_tecnico_cognome": "Mancini",
        "responsabile_tecnico_codice_fiscale": "MNCGVN80A01F205X",
        "responsabile_tecnico_qualifica": "perito industriale",
        "responsabile_tecnico_iscrizione_albo": "Collegio Periti Industriali Milano n. 1842",
        "settings": {
            "_seed": True,
            "_seed_script": "seed_demo_pipeline.py",
            "_seed_note": "Created by seed_demo_pipeline.py — safe to delete.",
        },
    }
    res = sb.table("tenants").upsert(row, on_conflict="vat_number").execute()
    tid = res.data[0]["id"]
    print(f"  ✓ tenant {tid} ({DEMO_BUSINESS_NAME})")
    return tid


def _upsert_territory(sb: Any, tenant_id: str) -> str:
    row = {
        "tenant_id": tenant_id,
        "type": "cap",
        "code": "20121",
        "name": "Milano Centro",
        "priority": 5,
    }
    res = sb.table("territories").upsert(row, on_conflict="tenant_id,type,code").execute()
    tid = res.data[0]["id"]
    print(f"  ✓ territory {tid} (CAP 20121 Milano)")
    return tid


def _reset_child_rows(sb: Any, tenant_id: str) -> None:
    """Delete child rows in FK-safe order. Tenant + territory preserved."""
    for table in (
        "practice_deadlines",
        "practice_events",
        "practice_documents",
        "practices",
        "lead_quotes",
        "outreach_sends",
        "events",
        "leads",
        "subjects",
        "roofs",
    ):
        sb.table(table).delete().eq("tenant_id", tenant_id).execute()
    # Reset practice counter so IDs start from 0001 again.
    sb.table("tenant_practice_counters").delete().eq("tenant_id", tenant_id).execute()
    sb.table("tenant_quote_counters").delete().eq("tenant_id", tenant_id).execute()
    print(f"  ✓ reset child rows for tenant {tenant_id}")


def _seed_leads(
    sb: Any,
    tenant_id: str,
    territory_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Seed all 10 companies. Returns mapping vat→{lead_id,roof_id,subject_id,...}."""
    result: dict[str, Any] = {}
    now = _now()

    for comp in COMPANIES:
        vat = comp["vat"]
        gh = _geohash(comp["lat"], comp["lng"])

        # ── Roof ────────────────────────────────────────────────────────
        roof_row = {
            "tenant_id": tenant_id,
            "territory_id": territory_id,
            "lat": comp["lat"],
            "lng": comp["lng"],
            "geohash": gh,
            "address": comp["address"],
            "cap": comp["cap"],
            "comune": comp["comune"],
            "provincia": comp["provincia"],
            "area_sqm": comp["area_sqm"],
            "estimated_kwp": comp["kwp"],
            "estimated_yearly_kwh": comp["kwh"],
            "exposure": "south",
            "pitch_degrees": 12.0,
            "shading_score": 0.85,
            "has_existing_pv": False,
            "data_source": "google_solar",
            "classification": "b2b",
            "status": "discovered",
            "scan_cost_cents": 0,
            "raw_data": {"_seed": True, "_script": "seed_demo_pipeline"},
        }
        roof_res = sb.table("roofs").upsert(roof_row, on_conflict="tenant_id,geohash").execute()
        roof_id = roof_res.data[0]["id"]

        # ── Subject ─────────────────────────────────────────────────────
        subject_row = {
            "tenant_id": tenant_id,
            "roof_id": roof_id,
            "type": "b2b",
            "business_name": comp["name"],
            "vat_number": vat,
            "ateco_description": comp["ateco"],
            "yearly_revenue_cents": comp["revenue_cents"],
            "employees": comp["employees"],
            "decision_maker_name": comp["decision_maker_name"],
            "decision_maker_role": comp["decision_maker_role"],
            "decision_maker_email": comp["decision_maker_email"],
            "decision_maker_email_verified": True,
            "data_sources": ["seed_demo_pipeline"],
            "enrichment_cost_cents": 0,
            "enrichment_completed_at": _iso(now),
            "pii_hash": _pii_hash(comp["name"], vat),
        }
        subject_res = sb.table("subjects").upsert(subject_row, on_conflict="tenant_id,roof_id").execute()
        subject_id = subject_res.data[0]["id"]

        # ── Lead ─────────────────────────────────────────────────────────
        slug = _slug(tenant_id, vat)
        lead_state = comp["lead_state"]
        score = comp["score"]
        tier = comp["tier"]
        feedback = comp.get("feedback")
        contract_value = comp.get("contract_value_cents")

        lead_row: dict[str, Any] = {
            "tenant_id": tenant_id,
            "roof_id": roof_id,
            "subject_id": subject_id,
            "public_slug": slug,
            "score": score,
            "score_tier": tier,
            "pipeline_status": lead_state,
            "outreach_channel": "email",
            "roi_data": {
                "annual_kwh": comp["kwh"],
                "kwp": comp["kwp"],
                "estimated_savings_eur": round(comp["kwh"] * 0.25, 0),
                "payback_years": 7.2,
                "roi_pct": 14.1,
            },
            "score_breakdown": {
                "size": min(score, 30),
                "location": min(score // 3, 25),
                "sector": min(score // 4, 25),
                "solar": 20,
            },
        }

        # Timestamp chain by status
        if lead_state in ("sent", "delivered", "opened", "clicked", "engaged",
                          "appointment", "closed_won", "closed_lost"):
            lead_row["outreach_sent_at"] = _days_ago(14)
        if lead_state in ("delivered", "opened", "clicked", "engaged",
                          "appointment", "closed_won", "closed_lost"):
            lead_row["outreach_delivered_at"] = _days_ago(13)
        if lead_state in ("opened", "clicked", "engaged",
                          "appointment", "closed_won", "closed_lost"):
            lead_row["outreach_opened_at"] = _days_ago(11)
        if lead_state in ("clicked", "engaged",
                          "appointment", "closed_won", "closed_lost"):
            lead_row["outreach_clicked_at"] = _days_ago(9)
        if lead_state in ("appointment", "closed_won", "closed_lost"):
            lead_row["dashboard_visited_at"] = _days_ago(7)

        if feedback:
            lead_row["feedback"] = feedback
            lead_row["feedback_at"] = _days_ago(3)
        if contract_value:
            lead_row["contract_value_cents"] = contract_value

        lead_res = sb.table("leads").upsert(lead_row, on_conflict="public_slug").execute()
        lead_id = lead_res.data[0]["id"]

        result[vat] = {
            "lead_id": lead_id,
            "roof_id": roof_id,
            "subject_id": subject_id,
            "slug": slug,
            "lead_state": lead_state,
            "company": comp["name"],
            "_is_champion": comp.get("_is_champion", False),
        }

    print(f"  ✓ {len(COMPANIES)} companies seeded (roofs + subjects + leads)")
    return result


def _seed_outreach_send(sb: Any, tenant_id: str, lead_id: str) -> str:
    """Create a realistic outreach_sends row for the 'sent' lead."""
    row = {
        "tenant_id": tenant_id,
        "lead_id": lead_id,
        "channel": "email",
        "template_id": "seed_demo_step1",
        "sequence_step": 1,
        "email_subject": "Risparmia fino al 30% sulla bolletta elettrica con il fotovoltaico",
        "email_message_id": f"<demo-{lead_id[:8]}@resend.dev>",
        "scheduled_for": _days_ago(14),
        "status": "delivered",
        "sent_at": _days_ago(14),
        "cost_cents": 1,
    }
    res = sb.table("outreach_sends").insert(row).execute()
    oid = res.data[0]["id"]
    print(f"  ✓ outreach_send {oid}")
    return oid


def _seed_quote(sb: Any, tenant_id: str, lead_id: str) -> str:
    """Create a lead_quote (preventivo) for the champion lead."""
    # Allocate a sequence number via RPC (same as production).
    seq_res = sb.rpc("next_quote_seq", {"p_tenant_id": tenant_id}).execute()
    seq = seq_res.data if isinstance(seq_res.data, int) else 1
    year = _now().year
    # Build a tenant abbreviation: first 4 consonants/letters of business name.
    abbr = "DEMO"
    pnumber = f"{abbr}/{year}/{seq:04d}"

    row = {
        "tenant_id": tenant_id,
        "lead_id": lead_id,
        "preventivo_number": pnumber,
        "preventivo_seq": seq,
        "version": 1,
        "status": "issued",
        "auto_fields": {
            "tenant_business_name": DEMO_BUSINESS_NAME,
            "tenant_piva": DEMO_TENANT_VAT.replace("IT", ""),
            "installazione_kwp": 224.0,
            "installazione_kwh_anno": 268_800.0,
            "risparmio_annuo_eur": 67_200.0,
            "payback_anni": 7.2,
        },
        "manual_fields": {
            "tech_marca_pannello": "Jinko Solar",
            "tech_modello_pannello": "Tiger Neo N-Type 72HL4-BDV 580W",
            "tech_potenza_pannello_w": 580,
            "tech_num_pannelli": 386,
            "tech_marca_inverter": "SolarEdge",
            "tech_modello_inverter": "SE220K",
            "tech_potenza_inverter_kw": 220,
            "tech_accumulo": False,
            "prezzo_netto_eur": 52400,
            "prezzo_iva_pct": 10,
            "prezzo_totale_eur": 57640,
            "pagamento_note": "30% anticipo · 40% SAL · 30% collaudo",
            "tempi_esecuzione_settimane": 8,
        },
        "pdf_url": None,
        "hero_url": None,
    }
    res = sb.table("lead_quotes").insert(row).execute()
    qid = res.data[0]["id"]
    print(f"  ✓ lead_quote {qid} ({pnumber})")
    return qid


def _seed_practice(
    sb: Any,
    tenant_id: str,
    lead_id: str,
    quote_id: str,
    full_render: bool,
) -> tuple[str, list[str]]:
    """Create a practice + 9 documents + 3 deadlines for the champion lead."""

    # Allocate practice number.
    seq_res = sb.rpc("next_practice_seq", {"p_tenant_id": tenant_id}).execute()
    seq = seq_res.data if isinstance(seq_res.data, int) else 1
    year = _now().year
    pnumber = f"DEMO/{year}/{seq:04d}"

    practice_row: dict[str, Any] = {
        "tenant_id": tenant_id,
        "lead_id": lead_id,
        "quote_id": quote_id,
        "practice_number": pnumber,
        "practice_seq": seq,
        "status": "documents_sent",
        "impianto_potenza_kw": 224.0,
        "impianto_pannelli_count": 386,
        "impianto_pod": "IT001E00000000",
        "impianto_distributore": "e_distribuzione",
        "impianto_data_inizio_lavori": "2026-03-10",
        "impianto_data_fine_lavori": "2026-04-15",
        "catastale_foglio": "42",
        "catastale_particella": "187",
        "catastale_subalterno": "3",
        "componenti_data": {
            "pannelli": {"marca": "Jinko Solar", "modello": "Tiger Neo 580W", "qty": 386, "kwp": 0.58},
            "inverter": {"marca": "SolarEdge", "modello": "SE220K", "kw": 220},
            "accumulo": None,
        },
        "data_snapshot": {"_seed": True},
        "extras": {
            "iban": "IT60X0542811101000000123456",
            "regime_ritiro": "ritiro_dedicato",
        },
    }

    practice_res = sb.table("practices").upsert(
        practice_row, on_conflict="lead_id"
    ).execute()
    practice_id = practice_res.data[0]["id"]
    print(f"  ✓ practice {practice_id} ({pnumber})")

    # ── Documents ──────────────────────────────────────────────────────────
    # Assign realistic statuses so the document list panel shows variety.
    DOC_STATUSES = {
        "dm_37_08": "reviewed",
        "comunicazione_comune": "sent",
        "modello_unico_p1": "sent",
        "modello_unico_p2": "draft",
        "schema_unifilare": "reviewed",
        "attestazione_titolo": "draft",
        "tica_areti": "sent",
        "transizione_50_ex_ante": "sent",
        "transizione_50_ex_post": "draft",
    }
    doc_ids: list[str] = []
    now_str = _iso(_now())
    for code in TEMPLATE_CODES:
        doc_status = DOC_STATUSES.get(code, "draft")
        doc_row: dict[str, Any] = {
            "practice_id": practice_id,
            "tenant_id": tenant_id,
            "template_code": code,
            "template_version": "v1",
            "status": doc_status,
            "pdf_url": None,
            "auto_data_snapshot": {"_seed": True},
            "manual_data": {},
            "generated_at": now_str if doc_status != "draft" else None,
            "sent_at": now_str if doc_status == "sent" else None,
        }
        doc_res = sb.table("practice_documents").upsert(
            doc_row, on_conflict="practice_id,template_code"
        ).execute()
        doc_ids.append(doc_res.data[0]["id"])

    print(f"  ✓ {len(doc_ids)} practice_documents")

    # ── Practice events ────────────────────────────────────────────────────
    _seed_practice_events(sb, tenant_id, practice_id, doc_ids)

    # ── Deadlines ──────────────────────────────────────────────────────────
    for dl in DEMO_DEADLINES:
        due_at = _days_from_now(dl["days_offset"])
        dl_row: dict[str, Any] = {
            "tenant_id": tenant_id,
            "practice_id": practice_id,
            "deadline_kind": dl["kind"],
            "due_at": due_at,
            "status": dl["status"],
            "satisfied_at": None,
            "metadata": {
                "title": dl["title"],
                "reference": dl["reference"],
                "_seed": True,
            },
        }
        sb.table("practice_deadlines").upsert(
            dl_row, on_conflict="practice_id,deadline_kind"
        ).execute()

    overdue = sum(1 for d in DEMO_DEADLINES if d["status"] == "overdue")
    open_dl = sum(1 for d in DEMO_DEADLINES if d["status"] == "open")
    print(f"  ✓ {len(DEMO_DEADLINES)} practice_deadlines ({overdue} overdue · {open_dl} open)")

    if full_render:
        _enqueue_render_tasks(tenant_id, practice_id)

    return practice_id, doc_ids


def _seed_practice_events(
    sb: Any, tenant_id: str, practice_id: str, doc_ids: list[str]
) -> None:
    """Insert a realistic event timeline for the practice."""
    now = _now()
    events: list[dict[str, Any]] = [
        {
            "tenant_id": tenant_id,
            "practice_id": practice_id,
            "event_type": "practice_created",
            "payload": {"_seed": True},
            "occurred_at": _iso(now - timedelta(days=20)),
        },
        {
            "tenant_id": tenant_id,
            "practice_id": practice_id,
            "document_id": doc_ids[0] if doc_ids else None,  # dm_37_08
            "event_type": "document_generated",
            "payload": {"template_code": "dm_37_08", "_seed": True},
            "occurred_at": _iso(now - timedelta(days=19)),
        },
        {
            "tenant_id": tenant_id,
            "practice_id": practice_id,
            "document_id": doc_ids[0] if doc_ids else None,
            "event_type": "document_reviewed",
            "payload": {"template_code": "dm_37_08", "_seed": True},
            "occurred_at": _iso(now - timedelta(days=18)),
        },
        {
            "tenant_id": tenant_id,
            "practice_id": practice_id,
            "document_id": doc_ids[1] if len(doc_ids) > 1 else None,  # comunicazione_comune
            "event_type": "document_sent",
            "payload": {
                "template_code": "comunicazione_comune",
                "channel": "pec",
                "_seed": True,
            },
            "occurred_at": _iso(now - timedelta(days=35)),  # 35 days ago → deadline is 5 days overdue
        },
        {
            "tenant_id": tenant_id,
            "practice_id": practice_id,
            "document_id": doc_ids[6] if len(doc_ids) > 6 else None,  # tica_areti
            "event_type": "document_sent",
            "payload": {
                "template_code": "tica_areti",
                "channel": "pec",
                "_seed": True,
            },
            "occurred_at": _iso(now - timedelta(days=56)),  # 56 days ago → 4 days left
        },
        {
            "tenant_id": tenant_id,
            "practice_id": practice_id,
            "document_id": doc_ids[7] if len(doc_ids) > 7 else None,  # transizione_50_ex_ante
            "event_type": "document_sent",
            "payload": {
                "template_code": "transizione_50_ex_ante",
                "channel": "pec",
                "_seed": True,
            },
            "occurred_at": _iso(now - timedelta(days=15)),  # 15 days ago → 45 days left
        },
        {
            "tenant_id": tenant_id,
            "practice_id": practice_id,
            "event_type": "deadline_created",
            "payload": {
                "deadline_kind": "comune_acceptance_30d",
                "due_at": _days_from_now(-5),
                "_seed": True,
            },
            "occurred_at": _iso(now - timedelta(days=35)),
        },
        {
            "tenant_id": tenant_id,
            "practice_id": practice_id,
            "event_type": "deadline_breached",
            "payload": {
                "deadline_kind": "comune_acceptance_30d",
                "days_overdue": 5,
                "_seed": True,
            },
            "occurred_at": _iso(now - timedelta(days=1)),
        },
    ]
    sb.table("practice_events").insert(events).execute()
    print(f"  ✓ {len(events)} practice_events")


def _enqueue_render_tasks(tenant_id: str, practice_id: str) -> None:
    """Enqueue arq render tasks for all practice documents.

    Requires REDIS_URL to be reachable and the arq worker to be running.
    Skip gracefully if arq / Redis is unavailable.
    """
    try:
        import asyncio

        from src.core.queue import get_pool

        async def _enqueue() -> None:
            pool = await get_pool()
            for code in TEMPLATE_CODES:
                await pool.enqueue_job(
                    "practice_render_document_task",
                    {
                        "practice_id": practice_id,
                        "template_code": code,
                        "tenant_id": tenant_id,
                    },
                )
            await pool.close()

        asyncio.run(_enqueue())
        print(f"  ✓ enqueued {len(TEMPLATE_CODES)} render tasks — start arq worker to process")
    except Exception as exc:  # noqa: BLE001
        print(
            f"  ⚠ --full-render: could not enqueue tasks ({exc}). "
            "Make sure Redis is running and REDIS_URL is set.\n"
            "  Start worker: cd apps/api && arq src.workers.main.WorkerSettings",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "--tenant-id",
        default=None,
        metavar="UUID",
        help="Target an existing tenant instead of creating Demo Solar Srl.",
    )
    ap.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Delete all child rows for the tenant before re-seeding. "
            "Tenant + territory are preserved."
        ),
    )
    ap.add_argument(
        "--full-render",
        action="store_true",
        help=(
            "Enqueue arq practice_render_document_task for every document. "
            "Requires Redis to be running and the arq worker to be active."
        ),
    )
    args = ap.parse_args()

    sb = get_service_client()
    print("\n══ Demo pipeline seeder ══")

    # ── 1. Tenant ──────────────────────────────────────────────────────────
    print("\n[1/6] Tenant")
    tenant_id = _get_or_create_tenant(sb, args.tenant_id)

    # ── 2. Reset ───────────────────────────────────────────────────────────
    if args.reset:
        print("\n[2/6] Reset child rows")
        _reset_child_rows(sb, tenant_id)
    else:
        print("\n[2/6] Reset skipped (pass --reset to wipe and re-seed fresh)")

    # ── 3. Territory ───────────────────────────────────────────────────────
    print("\n[3/6] Territory")
    territory_id = _upsert_territory(sb, tenant_id)

    # ── 4. Leads (roofs + subjects + leads + outreach_send) ────────────────
    print("\n[4/6] Leads")
    lead_map = _seed_leads(sb, tenant_id, territory_id)

    # Outreach send for the "sent" lead (Multilog)
    sent_entry = next((v for v in lead_map.values() if v["lead_state"] == "sent"), None)
    if sent_entry:
        _seed_outreach_send(sb, tenant_id, sent_entry["lead_id"])

    # ── 5. Quote + Practice (champion: Tessile Campana) ───────────────────
    champion = next((v for v in lead_map.values() if v.get("_is_champion")), None)
    if champion:
        print("\n[5/6] Quote (preventivo)")
        quote_id = _seed_quote(sb, tenant_id, champion["lead_id"])

        print("\n[6/6] Practice + documents + deadlines")
        practice_id, _ = _seed_practice(
            sb,
            tenant_id,
            champion["lead_id"],
            quote_id,
            full_render=args.full_render,
        )
    else:
        print("\n[5/6] Champion lead not found — skipping quote + practice", file=sys.stderr)
        practice_id = None

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"""
══ Done ══

Tenant:    {tenant_id}
Territory: CAP 20121 Milano
Leads:     {len(lead_map)} (spanning new · sent · delivered · opened · clicked ·
           engaged · appointment · closed_won · closed_lost · cold)
Practice:  {practice_id or 'N/A'}

Open dashboard → Leads, Pratiche, Scadenze to inspect.
""")
    if not args.full_render:
        print(
            "Tip: pass --full-render to enqueue real PDF renders.\n"
            "Requires: cd apps/api && arq src.workers.main.WorkerSettings\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
