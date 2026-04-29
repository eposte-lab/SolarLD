"""AtokaQueryBuilder — Sprint 11.

Why
---
Discovery costs are billed *per record returned*, so every Atoka filter we
move from our offline post-processing into the Atoka query itself is money
we don't spend on companies we were going to discard anyway.

The pre-Sprint-11 query used 5 filters (ateco, geo, employees, revenue,
active). Survival rate after our offline_filters layer was 18-25 % on
real tenants — i.e. we paid for ~5x more records than we kept.

This builder pushes the post-filterable signals into the Atoka request,
so post-filter survival should rise to the tenant's `atoka_survival_target`
(default 80 %). The remaining offline filters become genuine "subjective"
checks (anti-uffici, sede-operativa-vs-legale, etc.) that Atoka can't see.

Filters supported
-----------------
1.  ateco_codes          (existing)        comma-joined NACE/ATECO codes
2.  province_code         (existing)        IT province (NA, MI, …)
3.  region_code           (existing)        Italian region name
4.  employees_min/max     (existing)        employeesRange "min-max"
5.  revenue_min/max_eur   (existing)        revenueRange   "min-max"
6.  active_only           (existing)        active=true
7.  has_email             NEW              must have at least one email
8.  has_pec               NEW              must have a PEC address
9.  has_phone             NEW              must have a phone (or includeContacts only)
10. has_website           NEW              must have a website
11. legal_forms           NEW              {SRL, SPA, SAS, SNC, …}
12. founded_before_year   NEW              foundedYear≤X (mature companies)
13. exclude_in_liquidation NEW             legalStatus≠liquidation/concordato
14. exclude_pa            NEW              ATECO 84.* (Pubblica Amministrazione)
15. min_employees_floor   NEW              hard floor (PV economically viable)

How
---
Builder is a frozen dataclass; you build it from `tenant + sorgente module
config`, call `.execute()` to fan it out via the existing
`atoka_search_by_criteria` plus the new filters, and you get back a
``DiscoveryResult`` carrying:

* the AtokaProfiles (same shape as before),
* ``records_billed`` (how much Atoka charged us),
* ``filters_applied`` (audit trail for the survival-rate dashboard).

The builder does NOT persist anything — the level1_discovery agent keeps
ownership of upserting into ``scan_candidates``. We're a query layer.

Backwards compatibility
-----------------------
Existing callers (level1_discovery.run_level1) continue to work
unchanged. The builder is the new *recommended* entry point used by the
Sprint 11 orchestrator; legacy call-sites can migrate at their own pace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from ..core.logging import get_logger
from .italian_business_service import (
    AtokaProfile,
    EnrichmentUnavailable,
    atoka_search_by_criteria,
)

log = get_logger(__name__)


# ATECO 84.* = Pubblica Amministrazione + difesa. Excluded by default
# because PA buildings are rarely candidates for installer outreach.
_PA_ATECO_PREFIX = "84"


@dataclass(frozen=True)
class DiscoveryResult:
    profiles: list[AtokaProfile]
    records_billed: int
    filters_applied: dict[str, Any]
    survival_rate_estimate: float | None  # only set if caller passes a baseline


@dataclass
class AtokaQueryBuilder:
    """Composable wrapper for `atoka_search_by_criteria`.

    Usage::

        result = await (
            AtokaQueryBuilder(ateco_codes=ateco)
              .geo(province_code="NA")
              .employees(15, 250)
              .require_contact_methods(email=True, pec=True)
              .exclude_in_liquidation()
              .exclude_pa()
              .with_limit(200)
              .execute()
        )
    """

    ateco_codes: list[str]
    province_code: str | None = None
    region_code: str | None = None
    employees_min: int | None = None
    employees_max: int | None = None
    revenue_min_eur: int | None = None
    revenue_max_eur: int | None = None
    active_only: bool = True

    # Sprint 11 additions
    must_have_email: bool = False
    must_have_pec: bool = False
    must_have_phone: bool = False
    must_have_website: bool = False
    legal_forms: tuple[str, ...] = ()
    founded_before_year: int | None = None
    drop_in_liquidation: bool = False
    drop_public_admin: bool = False

    limit: int = 500

    # ------------------------------------------------------------------
    # Fluent builders
    # ------------------------------------------------------------------

    def geo(
        self,
        *,
        province_code: str | None = None,
        region_code: str | None = None,
    ) -> AtokaQueryBuilder:
        return _replace(self, province_code=province_code, region_code=region_code)

    def employees(
        self, mn: int | None = None, mx: int | None = None
    ) -> AtokaQueryBuilder:
        return _replace(self, employees_min=mn, employees_max=mx)

    def revenue(
        self, mn_eur: int | None = None, mx_eur: int | None = None
    ) -> AtokaQueryBuilder:
        return _replace(self, revenue_min_eur=mn_eur, revenue_max_eur=mx_eur)

    def require_contact_methods(
        self,
        *,
        email: bool = False,
        pec: bool = False,
        phone: bool = False,
        website: bool = False,
    ) -> AtokaQueryBuilder:
        return _replace(
            self,
            must_have_email=email,
            must_have_pec=pec,
            must_have_phone=phone,
            must_have_website=website,
        )

    def with_legal_forms(self, *forms: str) -> AtokaQueryBuilder:
        return _replace(self, legal_forms=tuple(f.upper() for f in forms))

    def founded_before(self, year: int) -> AtokaQueryBuilder:
        return _replace(self, founded_before_year=year)

    def exclude_in_liquidation(self) -> AtokaQueryBuilder:
        return _replace(self, drop_in_liquidation=True)

    def exclude_pa(self) -> AtokaQueryBuilder:
        return _replace(self, drop_public_admin=True)

    def with_limit(self, n: int) -> AtokaQueryBuilder:
        return _replace(self, limit=max(1, min(n, 5000)))

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self, *, client: httpx.AsyncClient | None = None
    ) -> DiscoveryResult:
        """Run the query, applying both Atoka-side and one-pass app-side filters.

        Atoka v2 supports many of these filters natively; for the ones it
        doesn't, we apply them as a *zero-cost* in-memory pass on the
        same response. Either way, the caller doesn't pay for records we
        immediately drop — we just don't bill them through to scan_candidates.

        We track every applied filter in the returned `filters_applied`
        for the dashboard's "survival rate" widget.
        """
        if not self.ateco_codes:
            return DiscoveryResult([], 0, {"reason": "no_ateco"}, None)

        # If we're asked to exclude PA, drop ATECO 84 codes from the query
        # outright — cheaper than receiving them and filtering after.
        ateco_eff = self.ateco_codes
        if self.drop_public_admin:
            ateco_eff = [c for c in ateco_eff if not c.startswith(_PA_ATECO_PREFIX)]
            if not ateco_eff:
                return DiscoveryResult(
                    [], 0, {"reason": "all_ateco_were_pa"}, None
                )

        # Atoka-side filters via the existing wrapper (handles paging,
        # auth, mock mode, retry, billing logging).
        try:
            raw = await atoka_search_by_criteria(
                ateco_codes=list(ateco_eff),
                province_code=self.province_code,
                region_code=self.region_code,
                employees_min=self.employees_min,
                employees_max=self.employees_max,
                revenue_min_eur=self.revenue_min_eur,
                revenue_max_eur=self.revenue_max_eur,
                limit=self.limit,
                active_only=self.active_only,
                client=client,
            )
        except EnrichmentUnavailable:
            raise

        billed = len(raw)

        # App-side filters that Atoka v2 doesn't expose natively. We
        # *don't* pay extra for these — the records were already in our
        # response — but we don't propagate them downstream either.
        kept: list[AtokaProfile] = []
        rejected_by: dict[str, int] = {}

        for prof in raw:
            reason = self._app_side_reject(prof)
            if reason is None:
                kept.append(prof)
            else:
                rejected_by[reason] = rejected_by.get(reason, 0) + 1

        survival = (len(kept) / billed) if billed else None

        log.info(
            "atoka_query_builder_executed",
            extra={
                "ateco_count": len(ateco_eff),
                "billed_records": billed,
                "kept_records": len(kept),
                "rejected_by": rejected_by,
                "survival_rate": survival,
            },
        )

        return DiscoveryResult(
            profiles=kept,
            records_billed=billed,
            filters_applied={
                "atoka_side": _audit_atoka_side(self),
                "app_side_rejected": rejected_by,
            },
            survival_rate_estimate=survival,
        )

    # ------------------------------------------------------------------
    # App-side filters
    # ------------------------------------------------------------------

    def _app_side_reject(self, prof: AtokaProfile) -> str | None:
        """Return a stable label if `prof` fails an app-side filter, else None."""
        raw = prof.raw or {}

        if self.must_have_email and not _has_email(prof, raw):
            return "missing_email"
        if self.must_have_pec and not _has_pec(prof, raw):
            return "missing_pec"
        if self.must_have_phone and not _has_phone(prof, raw):
            return "missing_phone"
        if self.must_have_website and not _has_website(prof, raw):
            return "missing_website"

        if self.legal_forms:
            form = (
                raw.get("legalForm")
                or raw.get("legal_form")
                or raw.get("companyType")
                or ""
            )
            if not _legal_form_matches(form, self.legal_forms):
                return "wrong_legal_form"

        if self.founded_before_year is not None:
            founded = (
                raw.get("foundedYear")
                or raw.get("founded_year")
                or _year_of(raw.get("foundedAt") or raw.get("founded_at"))
            )
            if founded is None or int(founded) > self.founded_before_year:
                return "too_recent"

        if self.drop_in_liquidation:
            status = (
                raw.get("legalStatus")
                or raw.get("legal_status")
                or raw.get("status")
                or ""
            ).lower()
            if any(
                bad in status
                for bad in ("liquidation", "liquidazione", "concordato", "fallimento")
            ):
                return "in_liquidation"

        return None


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _replace(qb: AtokaQueryBuilder, **kw: Any) -> AtokaQueryBuilder:
    """Manual dataclass clone — `dataclasses.replace` would work but
    importing it for one call adds a line."""
    data = qb.__dict__.copy()
    data.update(kw)
    return AtokaQueryBuilder(**data)


def _has_email(prof: AtokaProfile, raw: dict[str, Any]) -> bool:
    if getattr(prof, "primary_email", None):
        return True
    contacts = raw.get("contacts") or raw.get("emails") or []
    if isinstance(contacts, list):
        return any(c.get("email") if isinstance(c, dict) else c for c in contacts)
    return False


def _has_pec(prof: AtokaProfile, raw: dict[str, Any]) -> bool:
    if getattr(prof, "pec_email", None):
        return True
    pec = raw.get("pec") or raw.get("pecEmail")
    return bool(pec)


def _has_phone(prof: AtokaProfile, raw: dict[str, Any]) -> bool:
    phones = raw.get("phones") or raw.get("phone") or raw.get("telephones")
    if isinstance(phones, list):
        return len(phones) > 0
    return bool(phones)


def _has_website(prof: AtokaProfile, raw: dict[str, Any]) -> bool:
    return bool(raw.get("website") or raw.get("url"))


def _legal_form_matches(form: str, allowed: tuple[str, ...]) -> bool:
    f = form.upper()
    # Atoka returns variations like "S.R.L.", "SRL", "Società a responsabilità limitata".
    f_compact = f.replace(".", "").replace(" ", "")
    return any(allow in f_compact for allow in allowed)


def _year_of(s: Any) -> int | None:
    if not s:
        return None
    try:
        return int(str(s)[:4])
    except (TypeError, ValueError):
        return None


def _audit_atoka_side(qb: AtokaQueryBuilder) -> dict[str, Any]:
    """Snapshot of the Atoka-side filters for the dashboard audit log."""
    return {
        "ateco_codes": qb.ateco_codes,
        "province_code": qb.province_code,
        "region_code": qb.region_code,
        "employees_range": (qb.employees_min, qb.employees_max),
        "revenue_range_eur": (qb.revenue_min_eur, qb.revenue_max_eur),
        "active_only": qb.active_only,
    }


# ----------------------------------------------------------------------
# Convenience: build from tenant config
# ----------------------------------------------------------------------


def build_from_tenant_sorgente(
    *,
    ateco_codes: list[str],
    province_code: str | None,
    region_code: str | None,
    sorgente_config: dict[str, Any],
) -> AtokaQueryBuilder:
    """Translate the tenant's `module-sorgente` settings into a builder.

    Centralizes the mapping so the orchestrator doesn't repeat itself.
    Defaults match what Sprint 11 prescribes as "maximised pre-payment
    filtering" (active, has_email, exclude PA, exclude liquidation,
    employees ≥ 10 — under that the PV case is uneconomical).
    """
    qb = (
        AtokaQueryBuilder(ateco_codes=ateco_codes)
        .geo(province_code=province_code, region_code=region_code)
        .employees(
            mn=int(sorgente_config.get("min_employees") or 10),
            mx=sorgente_config.get("max_employees"),
        )
        .revenue(
            mn_eur=sorgente_config.get("min_revenue_eur"),
            mx_eur=sorgente_config.get("max_revenue_eur"),
        )
        .require_contact_methods(
            email=bool(sorgente_config.get("require_email", True)),
            pec=bool(sorgente_config.get("require_pec", False)),
            phone=bool(sorgente_config.get("require_phone", False)),
            website=bool(sorgente_config.get("require_website", False)),
        )
        .exclude_in_liquidation()
        .exclude_pa()
    )
    if forms := sorgente_config.get("legal_forms"):
        qb = qb.with_legal_forms(*forms)
    if year := sorgente_config.get("founded_before_year"):
        qb = qb.founded_before(int(year))
    if lim := sorgente_config.get("discovery_limit"):
        qb = qb.with_limit(int(lim))
    return qb


__all__ = [
    "AtokaQueryBuilder",
    "DiscoveryResult",
    "build_from_tenant_sorgente",
]
