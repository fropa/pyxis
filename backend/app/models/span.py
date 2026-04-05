import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Span(Base):
    """
    A single OpenTelemetry-style span.
    Root spans (parent_span_id is None) represent a full request.
    """

    __tablename__ = "spans"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id", ondelete="CASCADE"), index=True)

    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    span_id: Mapped[str] = mapped_column(String(64), index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(64))

    service: Mapped[str] = mapped_column(String(256), index=True)
    operation: Mapped[str] = mapped_column(String(512))  # e.g. "GET /api/users"

    duration_ms: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="ok")   # ok | error | unset
    status_code: Mapped[int | None] = mapped_column(Integer)         # HTTP status if applicable

    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_spans_tenant_service_started", "tenant_id", "service", "started_at"),
    )
