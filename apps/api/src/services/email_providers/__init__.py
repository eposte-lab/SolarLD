"""Email provider abstraction layer.

Sprint 6.1 — decouples the OutreachAgent from any single SMTP / HTTP
email vendor. Each provider implements ``EmailProvider`` (see
``base.py``) and registers itself with ``registry.get_provider(name)``.

Current providers:
    * ``resend``      — shared Resend HTTP API (default, transactional).
    * ``gmail_oauth`` — Google Workspace per-inbox OAuth2 (cold outreach).
    * ``m365_oauth``  — Microsoft Graph API Mail.Send (future).

Look up a provider for a given ``tenant_inboxes`` row via
``registry.get_provider(row['provider'])``.
"""

from .base import EmailProvider, SendResult
from .registry import get_provider, list_providers

__all__ = ["EmailProvider", "SendResult", "get_provider", "list_providers"]
