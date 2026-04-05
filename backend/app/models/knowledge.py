import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.core.database import Base

EMBED_DIM = 384  # BAAI/bge-small-en-v1.5 output dimension


class KnowledgeChunk(Base):
    """
    A chunk of customer IaC/pipeline knowledge stored with its embedding.
    This is the RAG corpus that the AI queries when analyzing incidents.
    """

    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id", ondelete="CASCADE"), index=True)

    source_type: Mapped[str] = mapped_column(String(32))
    # source_type: helm | terraform | ansible | github_actions | gitlab_ci | jenkinsfile | dockerfile | k8s_manifest

    repo_url: Mapped[str] = mapped_column(String(512))
    file_path: Mapped[str] = mapped_column(String(1024))
    chunk_index: Mapped[int] = mapped_column(default=0)  # which chunk within the file

    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(EMBED_DIM))

    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    # metadata: chart name, terraform resource type, job name, etc.

    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    file_hash: Mapped[str | None] = mapped_column(String(64))  # sha256, to skip re-indexing unchanged files


class KnowledgeSource(Base):
    """Tracks which repos have been connected and their indexing status."""

    __tablename__ = "knowledge_sources"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id", ondelete="CASCADE"), index=True)

    repo_url: Mapped[str] = mapped_column(String(512))
    repo_type: Mapped[str] = mapped_column(String(16), default="github")  # github | gitlab | gitea
    access_token: Mapped[str | None] = mapped_column(String(512))         # encrypted in prod

    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_commit_sha: Mapped[str | None] = mapped_column(String(64))
    index_status: Mapped[str] = mapped_column(String(16), default="pending")
    # index_status: pending | indexing | ready | error

    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
