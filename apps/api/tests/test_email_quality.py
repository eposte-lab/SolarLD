"""Contact-quality classifiers — placeholder + role mailbox detection."""

from __future__ import annotations

import pytest

from src.services.email_quality import (
    is_generic_mailbox,
    is_placeholder_email,
    is_role_mailbox,
)


@pytest.mark.parametrize(
    "email",
    [
        "info@azienda.it",  # info@ IS generic here (unlike is_role_mailbox)
        "contatti@azienda.it",
        "direzione@azienda.it",
        "acquisti@azienda.it",
        "amministrazione@azienda.it",
        "commerciale@azienda.it",
        "segreteria@hotel.it",
        "%20info@mechotel.com",  # scraper cruft normalised
        "dpo@x.it",  # existing role localparts still count
    ],
)
def test_is_generic_mailbox_true(email: str) -> None:
    assert is_generic_mailbox(email) is True


@pytest.mark.parametrize(
    "email",
    [
        "dante.mele@campaniaplastica.com",
        "stefano.marotta@center.it",
        "marconobis@caseificiocolonne.it",
        "g.rossi@azienda.it",
    ],
)
def test_is_generic_mailbox_false_for_personal(email: str) -> None:
    assert is_generic_mailbox(email) is False


@pytest.mark.parametrize(
    "email",
    [
        "a@a.it",  # single-char local + throwaway domain (real case: Famila)
        "tua@email.it",  # placeholder local + domain (real case: Ricars)
        "nome@azienda.it",
        "cognome@azienda.it",
        "your@company.com",
        "info@example.com",  # example domain substring
        "mario@esempio.it",
        "x@y",  # malformed / no real domain
        "noatsign",
    ],
)
def test_placeholder_emails_are_flagged(email: str) -> None:
    assert is_placeholder_email(email) is True


@pytest.mark.parametrize(
    "email",
    [
        "info@coemi-spareparts.com",  # real SME info@ — must NOT be flagged
        "info@yves-rocher.it",
        "s.tuccillo@montaninogroup.com",
        "silvano.caputo@mdspa.it",
        "deco4450@clienti-multicedi.com",  # per-store franchise — keep
    ],
)
def test_real_emails_are_not_placeholders(email: str) -> None:
    assert is_placeholder_email(email) is False


@pytest.mark.parametrize(
    "email",
    [
        "dpo@eurospin.it",  # Data Protection Officer — complaint magnet
        "privacy@cashpro.it",
        "servizioclienti@sole365.it",
        "noreply@chain.it",
        "no-reply@chain.it",
        "pec@azienda.it",
        "postmaster@x.it",
    ],
)
def test_role_mailboxes_are_flagged(email: str) -> None:
    assert is_role_mailbox(email) is True


@pytest.mark.parametrize(
    "email",
    [
        "info@pmi.it",  # normal SME mailbox — NOT a role inbox, keep it
        "commerciale@azienda.it",  # a real sales contact
        "vendite@azienda.it",
        "mario.rossi@azienda.it",
        "amministrazione@azienda.it",
    ],
)
def test_normal_mailboxes_are_not_role(email: str) -> None:
    assert is_role_mailbox(email) is False


def test_case_insensitive() -> None:
    assert is_role_mailbox("DPO@Eurospin.IT") is True
    assert is_placeholder_email("TUA@Email.it") is True
