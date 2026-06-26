"""Pre-validate the sendable backlog's contacts via NeverBounce — IN-PROCESS.

The send-time gate validates each address at send. This pre-validates the
warehouse backlog (``ready_to_send``/``picked``/``rendered`` … never sent) so
dead addresses are removed BEFORE they burn a daily-cap slot.

DRY-RUN by default: calls NeverBounce + prints the breakdown, writes nothing.
Pass --apply to actually exclude INVALID/DISPOSABLE leads (→ blacklisted).
Mirrors the send-time policy: UNKNOWN stays sendable (fail-open); VALID/CATCHALL
stay; only confirmed-bad addresses are removed.

Usage:
    apps/api/.venv/bin/python scripts/prevalidate_contacts.py            # dry-run
    apps/api/.venv/bin/python scripts/prevalidate_contacts.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

# Real keys first (root .env), then any local flags (apps/api/.env).
_API_DIR = Path(__file__).resolve().parent.parent
_ROOT = _API_DIR.parent.parent
load_dotenv(_ROOT / ".env", override=True)
load_dotenv(_API_DIR / ".env", override=True)
sys.path.insert(0, str(_API_DIR))

from src.services.contact_prevalidation_service import (  # noqa: E402
    run_contact_prevalidation,
)

TOTAL_TRADE = "df08df04-4c90-4613-b21e-80879fc958d1"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually exclude invalid leads")
    ap.add_argument("--tenant", default=TOTAL_TRADE)
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    res = await run_contact_prevalidation(
        tenant_id=args.tenant, limit=args.limit, dry_run=not args.apply
    )
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(
        f"\n[{mode}] scanned={res['scanned']} valid={res['valid']} "
        f"catchall={res['catchall']} unknown={res['unknown']} "
        f"excluded_invalid={res['excluded_invalid']} "
        f"excluded_disposable={res['excluded_disposable']} errored={res['errored']}"
    )
    for e in res.get("excluded_detail", []):
        print(f"  x {e['verdict']:10} {e['domain']:32} {e['business']}")


if __name__ == "__main__":
    asyncio.run(main())
