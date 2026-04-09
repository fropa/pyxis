import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Node(Base):
    """A node in the infrastructure graph: Linux host, K8s node, pod, service, cloud resource, etc."""

    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id", ondelete="CASCADE"), index=True)

    # Unique identifier within the tenant (e.g. k8s uid, hostname, ARN)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)

    name: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    # kind values: linux_host | k8s_node | k8s_pod | k8s_service | k8s_deployment |
    #              k8s_namespace | aws_ec2 | aws_rds | aws_alb | gcp_instance | etc.

    namespace: Mapped[str | None] = mapped_column(String(255))   # K8s namespace
    cluster: Mapped[str | None] = mapped_column(String(255))      # K8s cluster name

    status: Mapped[str] = mapped_column(String(32), default="unknown")
    # status: healthy | degraded | down | unknown

    labels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # Explicitly set on every agent heartbeat — reliable source of truth for liveness
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Edge(Base):
    """A directed connection between two nodes."""

    __tablename__ = "edges"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id", ondelete="CASCADE"), index=True)

    source_id: Mapped[str] = mapped_column(String, ForeignKey("nodes.id", ondelete="CASCADE"))
    target_id: Mapped[str] = mapped_column(String, ForeignKey("nodes.id", ondelete="CASCADE"))

    kind: Mapped[str] = mapped_column(String(64), default="network")
    # kind: network | dependency | calls | co-deployed | co-occurrence

    confidence: Mapped[float] = mapped_column(default=0.7)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    observation_count: Mapped[int] = mapped_column(default=1)

    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
