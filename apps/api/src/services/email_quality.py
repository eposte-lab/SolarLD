"""Outreach contact-quality classifiers.

Two cheap, pure checks applied at send time (2026-06-18) so we never burn a
send — or a NeverBounce credit — on an address that should never receive a
cold pitch:

* ``is_placeholder_email`` — junk the scraper swept up that isn't a real
  mailbox (``a@a.it``, ``tua@email.it``, ``nome@…``, ``info@example.com``).
  Mirrors the dashboard's ``looksLikeExampleEmail`` so display and send agree.
* ``is_role_mailbox`` — generic *role* inboxes that belong to a national
  chain's HQ rather than a buyable contact (``dpo@``, ``privacy@``,
  ``servizioclienti@``, ``noreply@``, ``pec@``…). NOTE: ``info@`` is **not**
  here — for a single SME it's the normal, legitimate mailbox we keep.
"""

from __future__ import annotations

# Local-parts that are obviously template placeholders, not real mailboxes.
# Kept in sync with apps/dashboard/src/lib/contatti-display.ts::looksLikeExampleEmail.
_PLACEHOLDER_LOCALPARTS: frozenset[str] = frozenset(
    {
        "tua",
        "latua",
        "la-tua",
        "nome",
        "cognome",
        "your",
        "user",
    }
)
# Substrings that mark an example/placeholder anywhere in the address.
_PLACEHOLDER_SUBSTRINGS: tuple[str, ...] = ("example", "esempio", "dummy", "placeholder")
# Throwaway domains the scraper occasionally captures from page templates.
_PLACEHOLDER_DOMAINS: frozenset[str] = frozenset(
    {"email.it", "example.com", "example.it", "dominio.it", "test.it", "test.com", "a.it"}
)

# Role / HQ inboxes that should never get a cold sales pitch. ``dpo`` /
# ``privacy`` are the GDPR contact — pitching them is a complaint magnet.
_ROLE_LOCALPARTS: frozenset[str] = frozenset(
    {
        "dpo",
        "privacy",
        "servizioclienti",
        "noreply",
        "no-reply",
        "donotreply",
        "pec",
        "postmaster",
        "abuse",
        "mailer-daemon",
        "mailerdaemon",
    }
)


def _split(email: str) -> tuple[str, str]:
    e = (email or "").strip().lower()
    local, _, domain = e.partition("@")
    return local, domain


def is_placeholder_email(email: str) -> bool:
    """True when the address is junk/placeholder rather than a real mailbox."""
    e = (email or "").strip().lower()
    if "@" not in e:
        return True
    local, domain = _split(e)
    if not local or not domain:
        return True
    if any(s in e for s in _PLACEHOLDER_SUBSTRINGS):
        return True
    if local in _PLACEHOLDER_LOCALPARTS:
        return True
    # Single-character local part (a@a.it) — never a real business mailbox.
    if len(local) <= 1:
        return True
    return domain in _PLACEHOLDER_DOMAINS


def is_role_mailbox(email: str) -> bool:
    """True for generic role/HQ inboxes (dpo@, privacy@, servizioclienti@, …).

    ``info@`` is deliberately NOT treated as a role mailbox: for a single SME
    it's the normal contact we want to keep.
    """
    local, _ = _split(email)
    return local in _ROLE_LOCALPARTS
