"""Request validation. Recipient errors belong to individual job results."""

from datetime import date
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class CertificateInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Text
    issuer: Text
    issued_on: date

    @field_validator("title", "issuer")
    @classmethod
    def printable(cls, value: str) -> str:
        if not value.isprintable():
            raise ValueError("Must contain printable characters only")
        return value


class Recipient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Text
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)]

    @field_validator("name")
    @classmethod
    def printable(cls, value: str) -> str:
        if not value.isprintable():
            raise ValueError("Must contain printable characters only")
        return value

    @field_validator("email")
    @classmethod
    def email_address(cls, value: str) -> str:
        # Basic contact-address validation; no mail is sent or DNS lookup made.
        import re

        if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", value):
            raise ValueError("Enter an email address such as name@example.com")
        local = value.split("@")[0]
        if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local:
            raise ValueError("Invalid email address")
        return value


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    certificate: CertificateInfo
    # Validate items independently so a malformed item cannot reject the batch.
    recipients: list[Any] = Field(min_length=1, max_length=1000)
