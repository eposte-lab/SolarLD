"""Lead schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .enums import InstallerFeedback


class LeadFeedback(BaseModel):
    feedback: InstallerFeedback
    notes: str | None = Field(default=None, max_length=2000)
    contract_value_eur: float | None = Field(default=None, ge=0)


class LeadPagination(BaseModel):
    page: int
    per_page: int
    total: int


class LeadListResponse(BaseModel):
    data: list[dict[str, Any]]
    pagination: LeadPagination
