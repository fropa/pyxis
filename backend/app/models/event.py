import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LogEvent(Base):
    """A single normalized log entry or infrastructure event."""

    __tablename__ = "log_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str | None] = mapped_column(String, ForeignKey("nodes.id", ondelete="SET NULL"), index=True)

    # When the event actually happened (from the log), vs when we received it
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped[str] = mapped_column(String(255))
    # source: syslog | k8s_event | ci_pipeline | app_log | audit_log | ...

    level: Mapped[str] = mapped_column(String(16), default="info")
    # level: debug | info | warning | error | critical

    raw: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Denormalized node name — lets us find logs even if node_id FK is NULL
    node_name: Mapped[str | None] = mapped_column(String(255), index=True)

    # Stable fingerprint — used for deduplication and rate windows
    fingerprint: Mapped[str | None] = mapped_column(String(512), index=True)

    # Flow-signal fields — extracted from parsed log content for topology/flow tracing
    request_id: Mapped[str | None] = mapped_column(String(255), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(255), index=True)
    client_ip: Mapped[str | None] = mapped_column(String(45))
    upstream_addr: Mapped[str | None] = mapped_column(String(512))
    response_time_ms: Mapped[float | None] = mapped_column()

    # Set by AI engine when it detects this event is interesting
    is_anomaly: Mapped[bool] = mapped_column(default=False)
    incident_id: Mapped[str | None] = mapped_column(String, ForeignKey("incidents.id", ondelete="SET NULL"))
