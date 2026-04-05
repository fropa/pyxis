import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DeployEvent(Base):
    __tablename__ = "deploy_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    service: Mapped[str] = mapped_column(String(256), index=True)
    version: Mapped[str | None] = mapped_column(String(128))
    deployed_by: Mapped[str | None] = mapped_column(String(256))
    environment: Mapped[str] = mapped_column(String(64), default="production")
    deployed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
