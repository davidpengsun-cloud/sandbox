from pydantic import BaseModel


class BearerToken(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class ReadableUnits(BaseModel):
    units: list[str]
