"""Closed requests and typed responses shared by access transports."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Limit = Annotated[int, Field(ge=1, le=1000)]


class GrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: list[str]
    name: str = ""


class Person(BaseModel):
    username: str
    name: str
    roles: list[str]
    updated_at: str | None = None
    updated_by: str | None = None


class Role(BaseModel):
    id: str
    label: str


class AccessResponse(BaseModel):
    people: list[Person]
    admins: list[str]
    changed_outside: bool
    roles: list[Role]


class AuditEntry(BaseModel):
    sequence: int
    at: str
    by: str
    via: Literal["api", "job", "migration"]
    username: str
    before: Person | None
    after: Person | None
    digest: str
    outside: bool = False
    changed_outside: bool = False


class AuditResponse(BaseModel):
    entries: list[AuditEntry]
    changed_outside: bool
