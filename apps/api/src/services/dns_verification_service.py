"""Live DNS verification for outreach domain setup.

Called during the onboarding wizard "Configure DNS" step and on-demand
via ``POST /v1/email-domains/{id}/dns-check``. Returns a structured
``DnsVerificationResult`` that the UI renders as a coloured traffic-light
for each record type.

Records checked
---------------
* **SPF** — TXT record at the apex (``@``) must include the required
  include directives for the chosen provider (Google / Resend / both).
* **DKIM** — CNAME at ``{selector}._domainkey.{domain}`` pointing at
  Resend's verification target (or the value Resend returns from its API).
  For Gmail the selector varies by Workspace setup; we check the most
  common ones (``google``, ``mail``, ``s1``, ``s2``).
* **DMARC** — TXT at ``_dmarc.{domain}`` starting with ``v=DMARC1``.
  We parse the ``p=`` policy and surface it.
* **Tracking CNAME** — if ``tracking_host`` is set, we check that
  ``{tracking_host}`` CNAMEs to ``track.solarld.app``.

All lookups use dnspython with a short timeout (3 s) so the endpoint
stays fast even against slow nameservers. We never block the send
pipeline on DNS state — verification is advisory; the UI nudges the
user to fix records.

Threading note: the dnspython Resolver is synchronous. We wrap each
query in ``asyncio.to_thread()`` so the FastAPI event loop isn't blocked.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import dns.exception
import dns.resolver

from ..core.logging import get_logger

log = get_logger(__name__)

# Canonical tracking target: all custom tracking hosts CNAME here.
TRACKING_CNAME_TARGET = "track.solarld.app"

# SPF includes required for each provider. We check that at least the
# relevant include is present in the SPF record; extra ones are fine.
_SPF_INCLUDES: dict[str, str] = {
    "resend": "_spf.resend.com",
    "google": "_spf.google.com",
    # Generic fallback: either SendGrid, AWS SES, etc. — not checked here.
}

# Known Google Workspace DKIM selectors. Order doesn't matter — we try all.
_GMAIL_DKIM_SELECTORS = ["google", "mail", "s1", "s2"]

# Resend DKIM selector format: ``resend._domainkey.{domain}``
_RESEND_DKIM_SELECTOR = "resend"

_TIMEOUT = 5  # seconds per DNS query


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class RecordStatus:
    """Result for a single DNS record check."""

    ok: bool                         # True = record found + valid
    found: bool                      # True = record exists (even if wrong value)
    value: str | None = None         # Raw value we found (or None)
    expected: str | None = None      # What we expected to find
    error: str | None = None         # Human-readable failure reason


@dataclass
class DnsVerificationResult:
    """Aggregate verification outcome for one domain."""

    domain: str
    spf: RecordStatus = field(default_factory=lambda: RecordStatus(ok=False, found=False))
    dkim_resend: RecordStatus = field(default_factory=lambda: RecordStatus(ok=False, found=False))
    dkim_google: RecordStatus = field(default_factory=lambda: RecordStatus(ok=False, found=False))
    dmarc: RecordStatus = field(default_factory=lambda: RecordStatus(ok=False, found=False))
    tracking_cname: RecordStatus = field(default_factory=lambda: RecordStatus(ok=False, found=False))
    dmarc_policy: str | None = None     # "none" | "quarantine" | "reject"

    @property
    def all_critical_ok(self) -> bool:
        """True when SPF + at least one DKIM variant + DMARC are all OK."""
        dkim_ok = self.dkim_resend.ok or self.dkim_google.ok
        return self.spf.ok and dkim_ok and self.dmarc.ok

    def to_dict(self) -> dict[str, Any]:
        def _s(r: RecordStatus) -> dict[str, Any]:
            return {
                "ok": r.ok,
                "found": r.found,
                "value": r.value,
                "expected": r.expected,
                "error": r.error,
            }
        return {
            "domain": self.domain,
            "all_critical_ok": self.all_critical_ok,
            "spf": _s(self.spf),
            "dkim_resend": _s(self.dkim_resend),
            "dkim_google": _s(self.dkim_google),
            "dmarc": _s(self.dmarc),
            "tracking_cname": _s(self.tracking_cname),
            "dmarc_policy": self.dmarc_policy,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def verify_domain(
    domain: str,
    *,
    tracking_host: str | None = None,
    dkim_resend_target: str | None = None,
) -> DnsVerificationResult:
    """Run all DNS checks for ``domain`` and return a structured result.

    Args:
        domain: Apex domain to check, e.g. ``"agendasolar.it"``.
        tracking_host: Custom tracking hostname, e.g. ``"go.agendasolar.it"``.
            Pass None to skip the CNAME check.
        dkim_resend_target: Expected CNAME target for Resend's DKIM record.
            When None we only check that the CNAME exists (not its value).
    """
    domain = domain.lower().strip().lstrip("@")
    result = DnsVerificationResult(domain=domain)

    # Run all lookups in parallel via asyncio.to_thread.
    spf_task = asyncio.create_task(_check_spf(domain))
    dkim_resend_task = asyncio.create_task(
        _check_dkim_resend(domain, dkim_resend_target)
    )
    dkim_google_task = asyncio.create_task(_check_dkim_google(domain))
    dmarc_task = asyncio.create_task(_check_dmarc(domain))
    tracking_task = asyncio.create_task(
        _check_tracking_cname(tracking_host) if tracking_host else _noop()
    )

    result.spf = await spf_task
    result.dkim_resend = await dkim_resend_task
    result.dkim_google = await dkim_google_task
    result.dmarc = await dmarc_task
    if tracking_host:
        result.tracking_cname = await tracking_task

    if result.dmarc.ok and result.dmarc.value:
        result.dmarc_policy = _parse_dmarc_policy(result.dmarc.value)

    log.info(
        "dns_verification.done",
        domain=domain,
        spf=result.spf.ok,
        dkim_resend=result.dkim_resend.ok,
        dkim_google=result.dkim_google.ok,
        dmarc=result.dmarc.ok,
        tracking=result.tracking_cname.ok if tracking_host else None,
        all_ok=result.all_critical_ok,
    )
    return result


# ---------------------------------------------------------------------------
# Individual checkers (synchronous, run in thread)
# ---------------------------------------------------------------------------


async def _check_spf(domain: str) -> RecordStatus:
    def _query() -> RecordStatus:
        try:
            answers = _txt_records(domain)
        except _NXDomain:
            return RecordStatus(ok=False, found=False, error="domain does not exist")
        except _NoAnswer:
            return RecordStatus(ok=False, found=False, error="no TXT records at apex")
        except _Timeout:
            return RecordStatus(ok=False, found=False, error="DNS timeout")

        # Find the SPF record (starts with v=spf1).
        spf_records = [r for r in answers if r.startswith("v=spf1")]
        if not spf_records:
            return RecordStatus(
                ok=False, found=False,
                error="no SPF TXT record (v=spf1) found at apex"
            )
        spf = spf_records[0]
        # Require at least one of the known include directives.
        for inc in _SPF_INCLUDES.values():
            if f"include:{inc}" in spf:
                return RecordStatus(ok=True, found=True, value=spf)
        return RecordStatus(
            ok=False, found=True, value=spf,
            expected="include:_spf.google.com or include:_spf.resend.com",
            error="SPF record found but missing required include directive",
        )

    return await asyncio.to_thread(_query)


async def _check_dkim_resend(
    domain: str, expected_target: str | None
) -> RecordStatus:
    """Check CNAME at ``resend._domainkey.{domain}``."""
    selector = f"{_RESEND_DKIM_SELECTOR}._domainkey.{domain}"

    def _query() -> RecordStatus:
        try:
            targets = _cname_record(selector)
        except _NXDomain:
            return RecordStatus(
                ok=False, found=False,
                expected=f"CNAME {selector} → {expected_target or 'resend DKIM target'}",
                error="CNAME record not found",
            )
        except (_NoAnswer, _Timeout) as exc:
            return RecordStatus(ok=False, found=False, error=str(exc))

        target = targets[0].rstrip(".")
        if expected_target:
            ok = target == expected_target.rstrip(".")
            return RecordStatus(
                ok=ok, found=True, value=target,
                expected=expected_target.rstrip("."),
                error=None if ok else "CNAME target mismatch",
            )
        # No expected target — just confirm the CNAME exists.
        return RecordStatus(ok=True, found=True, value=target)

    return await asyncio.to_thread(_query)


async def _check_dkim_google(domain: str) -> RecordStatus:
    """Check any of the common Google Workspace DKIM selectors."""
    for selector_name in _GMAIL_DKIM_SELECTORS:
        hostname = f"{selector_name}._domainkey.{domain}"

        def _query(h: str = hostname, s: str = selector_name) -> RecordStatus | None:
            # Check for CNAME first, then TXT (Google uses TXT for DKIM).
            for rtype in ("CNAME", "TXT"):
                try:
                    answers = _raw_records(h, rtype)
                    if answers:
                        val = answers[0]
                        return RecordStatus(
                            ok=True, found=True, value=val,
                            expected=f"{rtype} at {h}"
                        )
                except (_NXDomain, _NoAnswer):
                    continue
                except _Timeout:
                    return RecordStatus(ok=False, found=False, error=f"DNS timeout on {h}")
            return None

        res = await asyncio.to_thread(_query)
        if res and res.ok:
            return res

    return RecordStatus(
        ok=False, found=False,
        error=(
            f"No Google DKIM record found for selectors: "
            f"{', '.join(_GMAIL_DKIM_SELECTORS)}"
        ),
    )


async def _check_dmarc(domain: str) -> RecordStatus:
    hostname = f"_dmarc.{domain}"

    def _query() -> RecordStatus:
        try:
            answers = _txt_records(hostname)
        except _NXDomain:
            return RecordStatus(
                ok=False, found=False,
                expected="TXT v=DMARC1; p=none; rua=mailto:dmarc@...",
                error="_dmarc TXT record not found",
            )
        except _NoAnswer:
            return RecordStatus(ok=False, found=False, error="no TXT at _dmarc subdomain")
        except _Timeout:
            return RecordStatus(ok=False, found=False, error="DNS timeout")

        dmarc_records = [r for r in answers if "v=dmarc1" in r.lower()]
        if not dmarc_records:
            return RecordStatus(
                ok=False, found=True,
                value=answers[0] if answers else None,
                error="TXT record found but not a valid DMARC record",
            )
        return RecordStatus(ok=True, found=True, value=dmarc_records[0])

    return await asyncio.to_thread(_query)


async def _check_tracking_cname(tracking_host: str) -> RecordStatus:
    def _query() -> RecordStatus:
        try:
            targets = _cname_record(tracking_host)
        except _NXDomain:
            return RecordStatus(
                ok=False, found=False,
                expected=f"CNAME → {TRACKING_CNAME_TARGET}",
                error="CNAME record not found",
            )
        except (_NoAnswer, _Timeout) as exc:
            return RecordStatus(ok=False, found=False, error=str(exc))

        target = targets[0].rstrip(".")
        ok = target == TRACKING_CNAME_TARGET
        return RecordStatus(
            ok=ok, found=True, value=target,
            expected=TRACKING_CNAME_TARGET,
            error=None if ok else f"CNAME points to {target!r}, expected {TRACKING_CNAME_TARGET!r}",
        )

    return await asyncio.to_thread(_query)


async def _noop() -> RecordStatus:
    return RecordStatus(ok=True, found=True)


# ---------------------------------------------------------------------------
# DNS primitives (synchronous — intended to run via to_thread)
# ---------------------------------------------------------------------------


class _NXDomain(Exception):
    pass


class _NoAnswer(Exception):
    pass


class _Timeout(Exception):
    pass


def _resolver() -> dns.resolver.Resolver:
    r = dns.resolver.Resolver()
    r.lifetime = _TIMEOUT
    r.timeout = _TIMEOUT
    return r


def _txt_records(hostname: str) -> list[str]:
    try:
        answers = _resolver().resolve(hostname, "TXT")
        return [b"".join(rdata.strings).decode("utf-8", errors="replace")
                for rdata in answers]
    except dns.resolver.NXDOMAIN as exc:
        raise _NXDomain(f"NXDOMAIN: {hostname}") from exc
    except dns.resolver.NoAnswer as exc:
        raise _NoAnswer(f"No TXT answer: {hostname}") from exc
    except (dns.exception.Timeout, dns.resolver.Timeout) as exc:
        raise _Timeout(f"Timeout querying {hostname}") from exc
    except Exception as exc:  # noqa: BLE001
        raise _NoAnswer(f"DNS error on {hostname}: {exc}") from exc


def _cname_record(hostname: str) -> list[str]:
    try:
        answers = _resolver().resolve(hostname, "CNAME")
        return [str(r.target) for r in answers]
    except dns.resolver.NXDOMAIN as exc:
        raise _NXDomain(f"NXDOMAIN: {hostname}") from exc
    except dns.resolver.NoAnswer as exc:
        raise _NoAnswer(f"No CNAME answer: {hostname}") from exc
    except (dns.exception.Timeout, dns.resolver.Timeout) as exc:
        raise _Timeout(f"Timeout querying {hostname}") from exc
    except Exception as exc:  # noqa: BLE001
        raise _NoAnswer(f"DNS error on {hostname}: {exc}") from exc


def _raw_records(hostname: str, rtype: str) -> list[str]:
    try:
        answers = _resolver().resolve(hostname, rtype)
        if rtype == "TXT":
            return [b"".join(rd.strings).decode("utf-8", errors="replace")
                    for rd in answers]
        return [str(rd.target) if hasattr(rd, "target") else str(rd)
                for rd in answers]
    except dns.resolver.NXDOMAIN as exc:
        raise _NXDomain(f"NXDOMAIN: {hostname}") from exc
    except dns.resolver.NoAnswer as exc:
        raise _NoAnswer(f"No {rtype} answer: {hostname}") from exc
    except (dns.exception.Timeout, dns.resolver.Timeout) as exc:
        raise _Timeout(f"Timeout querying {hostname}") from exc
    except Exception as exc:  # noqa: BLE001
        raise _NoAnswer(f"DNS error on {hostname}: {exc}") from exc


def _parse_dmarc_policy(value: str) -> str | None:
    """Extract p= value from a DMARC record string."""
    for tag in value.split(";"):
        tag = tag.strip()
        if tag.lower().startswith("p="):
            return tag[2:].strip().lower()
    return None
