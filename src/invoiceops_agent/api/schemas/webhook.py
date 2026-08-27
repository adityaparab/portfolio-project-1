"""Email webhook schemas (stub email source — issue #12)."""

import base64
import binascii

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EmailAttachment(BaseModel):
    model_config = ConfigDict(frozen=True)

    filename: str
    content_type: str
    content_b64: str = Field(min_length=1)

    @field_validator("content_b64")
    @classmethod
    def _decodable(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("attachment.content_b64 is not valid base64") from exc
        return value

    def decode(self) -> bytes:
        return base64.b64decode(self.content_b64, validate=True)


class EmailWebhookPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    message_id: str = Field(min_length=1)
    received_at: str  # RFC 3339 timestamp from the (stub) email source
    attachment: EmailAttachment
