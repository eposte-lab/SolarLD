"""Symmetric encryption for OAuth refresh tokens (and other secrets).

Used by the email provider layer (Gmail/M365 OAuth) to keep refresh
tokens encrypted at rest in ``tenant_inboxes.oauth_refresh_token_encrypted``.
Never log the plaintext; only the provider dereferences it at send time.

Rationale for Fernet over raw AES:
    * Fernet is AES-128-CBC + HMAC-SHA256 with a versioned token format,
      so key rotation is a single-line change (``MultiFernet``).
    * The output is URL-safe base64 → drops cleanly into a ``text`` column.
    * ``cryptography`` is already a transitive dep (via
      ``python-jose[cryptography]``), so no new requirement.

Key source: ``settings.app_secret_key`` — a urlsafe-b64 32-byte key. In
dev you can seed it with ``Fernet.generate_key().decode()`` and drop it
into ``.env``. In prod the key lives in Railway secrets.

If the key is unset and encryption is requested, we raise — silently
returning plaintext into a "_encrypted" column would be a security trap.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)


class EncryptionError(Exception):
    """Raised when encrypt/decrypt fails (bad key, tampered ciphertext)."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = (settings.app_secret_key or "").strip()
    if not key:
        raise EncryptionError(
            "APP_SECRET_KEY is not configured. Generate one with "
            "`python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'` and set it in env."
        )
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:  # noqa: BLE001
        raise EncryptionError(
            f"APP_SECRET_KEY is not a valid Fernet key: {exc}. "
            "Must be urlsafe-base64-encoded 32 bytes."
        ) from exc


def encrypt(plaintext: str) -> str:
    """Return a URL-safe base64 Fernet token for ``plaintext``.

    Safe to round-trip through a ``text`` column; never raises on empty
    string (returns an empty-payload token) so callers don't have to
    short-circuit.
    """
    if plaintext is None:
        raise EncryptionError("cannot encrypt None")
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Return the plaintext for a Fernet ciphertext.

    Raises ``EncryptionError`` if the ciphertext is tampered or the key
    was rotated without a ``MultiFernet`` migration.
    """
    if not ciphertext:
        raise EncryptionError("cannot decrypt empty ciphertext")
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionError(
            "Fernet decryption failed — ciphertext tampered or key changed."
        ) from exc


def is_configured() -> bool:
    """True if APP_SECRET_KEY is set and valid. Never raises."""
    try:
        _fernet()
        return True
    except EncryptionError:
        return False
