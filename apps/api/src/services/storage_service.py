"""Supabase Storage helpers — upload/download media with signed URLs."""

from __future__ import annotations

from ..core.supabase_client import get_service_client


def upload_bytes(
    bucket: str,
    path: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    upsert: bool = True,
) -> str:
    """Upload raw bytes and return the public URL (or signed URL for private buckets)."""
    sb = get_service_client()
    sb.storage.from_(bucket).upload(
        path,
        data,
        {"content-type": content_type, "upsert": str(upsert).lower()},
    )
    # Public bucket → public URL; caller can switch to signed URL for private buckets.
    return sb.storage.from_(bucket).get_public_url(path)


def sign_url(bucket: str, path: str, expires_in: int = 3600) -> str:
    sb = get_service_client()
    res = sb.storage.from_(bucket).create_signed_url(path, expires_in)
    return res["signedURL"]
