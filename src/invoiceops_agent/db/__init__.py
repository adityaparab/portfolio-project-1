"""Database package: shared declarative base and the full data model.

One metadata source of truth for Alembic autogenerate and the application
repositories. Schema of record: docs/ARCHITECTURE.md §6.
"""

from invoiceops_agent.db import models
from invoiceops_agent.db.base import Base

__all__ = ["Base", "models"]
