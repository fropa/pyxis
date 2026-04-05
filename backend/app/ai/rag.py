"""
RAG retrieval — Pro tier feature.

In the starter tier fastembed is not installed, so this module
returns empty results gracefully. The AI engine handles empty
chunks fine — it just skips the IaC context section.

To enable (Pro tier):
    pip install fastembed==0.3.6
and set EMBED_MODEL in .env.
"""
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings

settings = get_settings()

# ── Optional fastembed import ─────────────────────────────────────────────────

try:
    from fastembed import TextEmbedding as _TextEmbedding
    _FASTEMBED_AVAILABLE = True
except ImportError:
    _FASTEMBED_AVAILABLE = False
    _TextEmbedding = None

_embedder = None


def get_embedder():
    global _embedder
    if not _FASTEMBED_AVAILABLE:
        return None
    if _embedder is None:
        _embedder = _TextEmbedding(model_name=settings.EMBED_MODEL)
    return _embedder


def embed_text(text_input: str) -> list[float] | None:
    embedder = get_embedder()
    if embedder is None:
        return None
    embeddings = list(embedder.embed([text_input]))
    return embeddings[0].tolist()


async def retrieve_relevant_chunks(
    query: str,
    tenant_id: str,
    db: AsyncSession,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """
    Return top_k most relevant knowledge chunks.
    Returns empty list if fastembed is not installed (starter tier).
    """
    if not _FASTEMBED_AVAILABLE:
        return []

    from sqlalchemy import text

    k = top_k or settings.RAG_TOP_K
    query_embedding = embed_text(query)
    if query_embedding is None:
        return []

    result = await db.execute(
        text("""
            SELECT id, source_type, repo_url, file_path, content, metadata,
                   1 - (embedding <=> :embedding::vector) AS similarity
            FROM knowledge_chunks
            WHERE tenant_id = :tenant_id
            ORDER BY embedding <=> :embedding::vector
            LIMIT :limit
        """),
        {
            "embedding": str(query_embedding),
            "tenant_id": tenant_id,
            "limit": k,
        },
    )

    rows = result.mappings().all()
    return [
        {
            "id": r["id"],
            "source_type": r["source_type"],
            "repo_url": r["repo_url"],
            "file_path": r["file_path"],
            "content": r["content"],
            "metadata": r["metadata"],
            "similarity": float(r["similarity"]),
        }
        for r in rows
    ]
