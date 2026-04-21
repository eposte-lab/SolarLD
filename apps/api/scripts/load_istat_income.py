"""One-shot loader for `geo_income_stats` from an ISTAT/MEF CSV dump.

Input: a CSV with one row per CAP. Expected columns (case-insensitive):

    cap,provincia,regione,comune,reddito_medio_eur,popolazione,case_unifamiliari_pct

Extra columns are accepted and packed into `source_metadata` so the
ISTAT vintage (e.g. "2023_redditi_cap") + smoothing notes are visible
in the DB without a schema migration every reload.

Where to get the dataset:
  * MEF "Dichiarazioni IRPEF per comune e CAP" (annual)
  * ISTAT "Popolazione residente — dettaglio per CAP"
  * ISTAT "Censimento permanente della popolazione e delle abitazioni"
    → single-family dwelling share

Producing the CSV from those three tables is outside this script —
we expect a data-ops process (or a manual spreadsheet merge) to hand
us the pre-joined file. Kept the loader dumb on purpose: the moment
the joiner logic lives here is the moment it becomes a maintenance
debt across five data vintages.

Usage:
    .venv/bin/python scripts/load_istat_income.py path/to/istat.csv
    .venv/bin/python scripts/load_istat_income.py file.csv --vintage 2023
    .venv/bin/python scripts/load_istat_income.py file.csv --dry
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.supabase_client import get_service_client  # noqa: E402


# Columns we recognise (all others get folded into source_metadata).
_CORE_COLS = {
    "cap",
    "provincia",
    "regione",
    "comune",
    "reddito_medio_eur",
    "popolazione",
    "case_unifamiliari_pct",
}


def _norm_key(k: str) -> str:
    return k.strip().lower().replace(" ", "_")


def _parse_int(raw: str | None) -> int | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        # Accept "34.000" (dot thousands) and "34,000" (comma thousands).
        cleaned = raw.replace(".", "").replace(",", "").strip()
        return int(cleaned)
    except (ValueError, AttributeError):
        return None


def _row_to_upsert(row: dict[str, str], vintage: str) -> dict[str, Any] | None:
    """Shape a CSV row into a Supabase upsert payload. Returns None if
    the row lacks a CAP (malformed)."""
    norm = {_norm_key(k): v for k, v in row.items()}
    cap = (norm.get("cap") or "").strip()
    if not cap:
        return None

    meta = {
        k: v for k, v in norm.items() if k not in _CORE_COLS and v is not None
    }
    meta["vintage"] = vintage
    meta["loaded_at"] = datetime.now(tz=timezone.utc).isoformat()

    return {
        "cap": cap,
        "provincia": (norm.get("provincia") or "").strip().upper()[:3],
        "regione": (norm.get("regione") or "").strip(),
        "comune": (norm.get("comune") or "").strip() or None,
        "reddito_medio_eur": _parse_int(norm.get("reddito_medio_eur")),
        "popolazione": _parse_int(norm.get("popolazione")),
        "case_unifamiliari_pct": _parse_int(norm.get("case_unifamiliari_pct")),
        "source_metadata": meta,
        "updated_at": meta["loaded_at"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv_path", help="Path to the pre-joined ISTAT CSV.")
    ap.add_argument(
        "--vintage",
        default="unknown",
        help="Label embedded in source_metadata for auditing (e.g. '2023').",
    )
    ap.add_argument(
        "--batch",
        type=int,
        default=500,
        help="Upsert batch size (Supabase has a ~1MB request cap).",
    )
    ap.add_argument(
        "--dry", action="store_true", help="Parse only; don't write."
    )
    args = ap.parse_args()

    path = Path(args.csv_path)
    if not path.exists():
        print(f"CSV not found: {path}", file=sys.stderr)
        return 2

    sb = get_service_client()

    total = 0
    skipped = 0
    buf: list[dict[str, Any]] = []

    # DictReader auto-handles BOM in UTF-8 CSVs emitted by Excel.
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            upsert = _row_to_upsert(row, vintage=args.vintage)
            if upsert is None:
                skipped += 1
                continue
            buf.append(upsert)
            if len(buf) >= args.batch:
                _flush(sb, buf, dry=args.dry)
                total += len(buf)
                buf.clear()

    if buf:
        _flush(sb, buf, dry=args.dry)
        total += len(buf)

    print(
        f"Done. upserted={total} skipped={skipped} vintage={args.vintage} dry={args.dry}"
    )
    return 0


def _flush(sb: Any, rows: list[dict[str, Any]], *, dry: bool) -> None:
    if dry:
        print(f"  [dry] {len(rows)} rows (first CAP={rows[0]['cap']})")
        return
    sb.table("geo_income_stats").upsert(rows, on_conflict="cap").execute()


if __name__ == "__main__":
    raise SystemExit(main())
