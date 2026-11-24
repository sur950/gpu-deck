"""
Auth request/response schemas (libs/contracts/openapi/admin.yaml: Auth tag).
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., max_length=100)
    password: str = Field(..., min_length=1, max_length=200)


class AdminUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str = Field(..., max_length=100)
    display_name: Optional[str] = None
    password_changed_at: datetime
    last_login_at: Optional[datetime] = None
    last_login_ip: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class LoginResult(BaseModel):
    session_id: UUID
    expires_at: datetime
    expires_in: int = Field(..., description="Seconds")
    csrf_token: str
    user: AdminUser
    must_change_password: bool = False


class CsrfToken(BaseModel):
    csrf_token: str
    header_name: str = "X-CSRF-Token"
    cookie_name: str = "gpudeck_csrf"


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(..., max_length=200)
    new_password: str = Field(..., min_length=12, max_length=200)


class Session(BaseModel):
    id: UUID
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: datetime
    last_active_at: Optional[datetime] = None
    expires_at: datetime
    is_current: bool
