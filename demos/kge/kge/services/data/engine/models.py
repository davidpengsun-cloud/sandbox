from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProductRows(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit: str = Field(min_length=1)
    table: str = Field(min_length=1)
    limit: int = Field(default=20, ge=1, le=100)


class ProductExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit: str = Field(min_length=1)
    table: str = Field(min_length=1)


class OwnQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit: str = Field(min_length=1)
    sql: str = Field(min_length=1)


class RowsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    capped_at: int
    run_trace: dict[str, Any]


class ExportResponse(RowsResponse):
    filename: str
