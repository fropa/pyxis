import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Runbook(Base):
    __tablename__ = "runbooks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    incident_id: Mapped[str] = mapped_column(String, ForeignKey("incidents.id", ondelete="CASCADE"), index=True, unique=True)
    title: Mapped[str] = mapped_column(String(512))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
