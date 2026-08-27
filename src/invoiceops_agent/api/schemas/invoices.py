"""Invoice resource schemas."""

from pydantic import BaseModel, ConfigDict, Field


class InvoiceAccepted(BaseModel):
    """201 response for POST /v1/invoices (new document accepted)."""

    model_config = ConfigDict(frozen=True)

    invoice_id: int
    run_id: int
    content_hash: str = Field(min_length=64, max_length=64)
    status: str = "RECEIVED"
    duplicate: bool = False
