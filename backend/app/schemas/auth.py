"""Auth request/response contracts (CLAUDE.md §9)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Role = Literal["user", "admin"]


class RegisterRequest(BaseModel):
    # extra="forbid": a body carrying "role" is a 422, so registration can never
    # choose a role (§8).
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Deliberately not EmailStr: the seeded admin may use a special-use domain such as
    # admin@veridoc.local (the .env.example default), which EmailStr rejects. Login only
    # needs a lookup key, and a bad address simply fails to match.
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    role: Role


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: Role
    created_at: datetime
