"""Territory schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .enums import TerritoryType


class TerritoryCreate(BaseModel):
    type: TerritoryType
    code: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1, max_length=128)
    bbox: dict[str, Any] | None = None
    priority: int = Field(default=5, ge=1, le=10)
    excluded: bool = False


class TerritoryOut(BaseModel):
    id: str
    tenant_id: str
    type: TerritoryType
    code: str
    name: str
    bbox: dict[str, Any] | None = None
    excluded: bool
    priority: int
    created_at: datetime
    updated_at: datetime
