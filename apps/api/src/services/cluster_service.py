"""Cluster signature computation for lead segmentation.

Each lead is assigned a short deterministic string that identifies
which copy cluster it belongs to.  The cluster_signature is used by
the A/B engine (cluster_copy_variants) to maintain separate variant
pairs per segment — so an installer with both construction-CEO leads
and retail-CFO leads gets independently-optimised copy for each.

B2B formula:  ateco{2digit}_{employees_bucket}_{dm_role_normalised}
              e.g. "ateco41_m_ceo", "ateco47_xl_direttore"

B2C formula:  b2c_{postal_province_lower}
              e.g. "b2c_na", "b2c_rm"

Fallback:     "default"  (used when not enough fields are present)

The signature is intentionally short (≤40 chars) and ASCII-safe so it
works as a PostgreSQL partial index key without escaping hassle.
"""

from __future__ import annotations

import re


# ── Employee buckets ──────────────────────────────────────────────────
# Thresholds chosen to match common Atoka employee range breakpoints.
_EMPLOYEE_THRESHOLDS = (
    (10,  "s"),    # 1-10
    (25,  "m"),    # 11-25
    (50,  "l"),    # 26-50
    (100, "xl"),   # 51-100
)
_EMPLOYEE_BUCKET_XXL = "xxl"  # 101+


def _employees_bucket(employees: int | float | None) -> str:
    if not employees or employees <= 0:
        return "s"
    n = int(employees)
    for threshold, bucket in _EMPLOYEE_THRESHOLDS:
        if n <= threshold:
            return bucket
    return _EMPLOYEE_BUCKET_XXL


# ── DM role normalisation ─────────────────────────────────────────────
# Map Italian / English decision-maker role strings to short slugs that
# won't explode the signature length or contain special chars.
_ROLE_MAP: dict[str, str] = {
    # CEO / Owner variants
    "ceo": "ceo",
    "amministratore delegato": "ceo",
    "ad": "ceo",
    "founder": "ceo",
    "titolare": "ceo",
    "proprietario": "ceo",
    "owner": "ceo",
    # CFO / Finance
    "cfo": "cfo",
    "direttore finanziario": "cfo",
    "responsabile finanza": "cfo",
    # COO / Operations
    "coo": "coo",
    "direttore operativo": "coo",
    "responsabile operations": "coo",
    # Technical / Engineering
    "cto": "cto",
    "direttore tecnico": "cto",
    "responsabile tecnico": "cto",
    "technical director": "cto",
    # HR
    "direttore risorse umane": "hr",
    "responsabile hr": "hr",
    # Procurement / Sustainability
    "responsabile acquisti": "proc",
    "direttore acquisti": "proc",
    "responsabile sostenibilita": "sust",
    "responsabile esg": "sust",
    # Facility / Energy
    "facility manager": "fac",
    "energy manager": "fac",
    "responsabile facility": "fac",
    # General Management
    "direttore generale": "gm",
    "general manager": "gm",
    "manager": "mgr",
    "dirigente": "dir",
    "responsabile": "resp",
}


def _normalise_role(role: str | None) -> str:
    if not role:
        return "unknown"
    normalised = role.strip().lower()
    # Try exact match first.
    if normalised in _ROLE_MAP:
        return _ROLE_MAP[normalised]
    # Partial match — first token wins (e.g. "Direttore Generale Operativo" → "gm").
    for key, slug in _ROLE_MAP.items():
        if key in normalised:
            return slug
    # Fallback: first word, max 8 chars, ASCII-only, lowercase.
    first_word = re.split(r"\s+", normalised)[0]
    sanitised = re.sub(r"[^a-z0-9]", "", first_word)[:8]
    return sanitised or "unknown"


# ── ATECO normalisation ────────────────────────────────────────────────

def _ateco_2digit(ateco_code: str | None) -> str | None:
    """Extract the leading 2-digit ATECO division from a code like '41.10'."""
    if not ateco_code:
        return None
    cleaned = re.sub(r"[^0-9]", "", ateco_code.strip())
    if len(cleaned) < 2:
        return None
    return cleaned[:2]


# ── Public entry ────────────────────────────────────────────────────────

def compute_cluster_signature(subject: dict) -> str:
    """Return the cluster_signature for a lead's subject (company data).

    ``subject`` is the subjects row dict from Supabase, expected to
    contain B2B fields (ateco_code, employees, decision_maker_role) or
    the B2C postal_province.

    Returns a short ASCII string, max ~40 chars.  Never raises.
    """
    ateco_code = subject.get("ateco_code")
    employees = subject.get("employees")
    dm_role = subject.get("decision_maker_role")
    postal_province = subject.get("postal_province")

    # ── B2B path ────────────────────────────────────────────────────
    ateco = _ateco_2digit(ateco_code)
    if ateco:
        bucket = _employees_bucket(employees)
        role = _normalise_role(dm_role)
        return f"ateco{ateco}_{bucket}_{role}"

    # ── B2C path ────────────────────────────────────────────────────
    province = (postal_province or "").strip().lower()
    province = re.sub(r"[^a-z0-9]", "", province)
    if province:
        return f"b2c_{province[:4]}"

    # ── Fallback ────────────────────────────────────────────────────
    return "default"


def describe_cluster(cluster_signature: str) -> str:
    """Return a human-readable Italian description for a cluster signature.

    Used by the variant_generator_service to provide context to Claude
    Haiku when generating copy.
    """
    if cluster_signature == "default":
        return "Profilo generico"

    if cluster_signature.startswith("b2c_"):
        province = cluster_signature[4:].upper()
        return f"Residenziale B2C · provincia {province}"

    if cluster_signature.startswith("ateco"):
        parts = cluster_signature.split("_")
        if len(parts) >= 3:
            ateco_raw = parts[0].replace("ateco", "")
            emp_raw = parts[1] if len(parts) > 1 else "?"
            role_raw = "_".join(parts[2:]) if len(parts) > 2 else "sconosciuto"

            emp_labels = {
                "s": "1-10 dipendenti",
                "m": "11-25 dipendenti",
                "l": "26-50 dipendenti",
                "xl": "51-100 dipendenti",
                "xxl": "100+ dipendenti",
            }
            emp_label = emp_labels.get(emp_raw, f"~{emp_raw} dipendenti")

            return f"B2B · ATECO {ateco_raw} · {emp_label} · decisore: {role_raw}"

    return cluster_signature
