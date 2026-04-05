"""
Indexer: crawl a repo → chunk files → embed → store in pgvector.
"""
import os
import tempfile
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.knowledge import KnowledgeChunk, KnowledgeSource
from app.knowledge.crawler import clone_or_update, iter_files, sha256
from app.ai.rag import embed_text

CHUNK_SIZE = 800      # chars per chunk
CHUNK_OVERLAP = 100   # overlap between chunks


def _chunk_text(text: str) -> list[str]:
    """Simple sliding-window chunker."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append(text[start:end])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return [c.strip() for c in chunks if c.strip()]


async def index_repository(source_id: str, tenant_id: str) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(KnowledgeSource).where(KnowledgeSource.id == source_id)
        )
        source = result.scalar_one_or_none()
        if not source:
            return

        source.index_status = "indexing"
        await db.commit()

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                repo = clone_or_update(source.repo_url, source.access_token, tmp_dir)
                current_sha = repo.head.commit.hexsha

                # Skip if nothing changed since last index
                if source.last_commit_sha == current_sha:
                    source.index_status = "ready"
                    await db.commit()
                    return

                # Delete old chunks for this source
                await db.execute(
                    delete(KnowledgeChunk).where(
                        KnowledgeChunk.tenant_id == tenant_id,
                        KnowledgeChunk.repo_url == source.repo_url,
                    )
                )
                await db.flush()

                # Index new chunks
                count = 0
                for source_type, rel_path, content in iter_files(tmp_dir):
                    file_hash = sha256(content)
                    chunks = _chunk_text(content)

                    for i, chunk in enumerate(chunks):
                        embedding = embed_text(chunk)
                        db.add(KnowledgeChunk(
                            id=str(uuid.uuid4()),
                            tenant_id=tenant_id,
                            source_type=source_type,
                            repo_url=source.repo_url,
                            file_path=rel_path,
                            chunk_index=i,
                            content=chunk,
                            embedding=embedding,
                            metadata_={
                                "source_type": source_type,
                                "file": rel_path,
                                "chunk": i,
                                "total_chunks": len(chunks),
                            },
                            file_hash=file_hash,
                        ))
                        count += 1

                        # Flush in batches to avoid huge transactions
                        if count % 100 == 0:
                            await db.flush()

                await db.commit()

                source.index_status = "ready"
                source.last_indexed_at = datetime.now(timezone.utc)
                source.last_commit_sha = current_sha
                source.error_message = None
                await db.commit()

        except Exception as exc:
            source.index_status = "error"
            source.error_message = str(exc)
            await db.commit()
            raise
