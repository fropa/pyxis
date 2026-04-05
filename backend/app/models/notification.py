import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NotificationChannel(Base):
    """A configured notification destination for a tenant."""

    __tablename__ = "notification_channels"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32))
    # kind: slack | webhook | email

    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # slack:   {"webhook_url": "https://hooks.slack.com/..."}
    # webhook: {"url": "...", "headers": {...}}
    # email:   {"to": "...", "smtp_host": "...", "smtp_port": 587, "username": "...", "password": "..."}

    # Which severity levels trigger this channel
    min_severity: Mapped[str] = mapped_column(String(16), default="medium")
    # Which event types trigger this channel
    event_types: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["incident_opened", "rca_ready"])

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
