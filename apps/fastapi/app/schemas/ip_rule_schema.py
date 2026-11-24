"""
IP rule schemas (admin.yaml: IP Rules tag).
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IpRuleAction(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"


class IpRuleScope(str, Enum):
    ADMIN = "admin"
    API_LAYER = "api_layer"


def _validate_cidr(value: str) -> str:
    import ipaddress

    try:
        return str(ipaddress.ip_network(value.strip(), strict=False))
    except ValueError as exc:
        raise ValueError("must be an IPv4/IPv6 address or CIDR") from exc


class IpRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: IpRuleAction
    scope: IpRuleScope
    cidr: str
    description: Optional[str] = Field(None, max_length=512)
    enabled: bool = True
    expires_at: Optional[datetime] = None

    _cidr = field_validator("cidr")(_validate_cidr)


class IpRuleUpdate(BaseModel):
    """Partial update; every field optional."""

    model_config = ConfigDict(extra="forbid")

    action: Optional[IpRuleAction] = None
    scope: Optional[IpRuleScope] = None
    cidr: Optional[str] = None
    description: Optional[str] = Field(None, max_length=512)
    enabled: Optional[bool] = None
    expires_at: Optional[datetime] = None

    @field_validator("cidr")
    @classmethod
    def _cidr(cls, value: Optional[str]) -> Optional[str]:
        return _validate_cidr(value) if value is not None else None


class IpRule(BaseModel):
    id: UUID
    action: IpRuleAction
    scope: IpRuleScope
    cidr: str
    description: Optional[str] = None
    enabled: bool
    expires_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class IpCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ip: str
    scope: IpRuleScope

    @field_validator("ip")
    @classmethod
    def _ip(cls, value: str) -> str:
        import ipaddress

        try:
            return str(ipaddress.ip_address(value.strip()))
        except ValueError as exc:
            raise ValueError("must be an IPv4/IPv6 address") from exc


class IpCheckResult(BaseModel):
    ip: str
    scope: IpRuleScope
    allowed: bool
    matched_rule: Optional[IpRule] = None
    reason: str
