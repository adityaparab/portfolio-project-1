"""Declarative base with shared column conventions."""

from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[type, DateTime]] = {
        datetime: DateTime(timezone=True),
    }
