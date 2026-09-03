# knowledge_base feature: MongoDB vector search RAG retrieval

from typing import Any
from core.database import mongo


async def retrieve_rag_chunks(query_embedding: list[float], top_k: int = 5) -> list[dict[str, Any]]:
    """
    Perform a MongoDB Atlas Vector Search on the 'knowledge_chunks' collection.
    Requires Atlas Search index named 'knowledge_vector_index' with
    field 'embedding' of type 'knnVector' (dimensions must match your model).
    """
    if mongo.db is None:
        return []

    pipeline = [
        {
            "$vectorSearch": {
                "index": "knowledge_vector_index",
                "path": "embedding",
                "queryVector": query_embedding,
                "numCandidates": top_k * 10,
                "limit": top_k,
            }
        },
        {
            "$project": {
                "_id": 0,
                "text": 1,
                "source": 1,
                "score": {"$meta": "vectorSearchScore"},
            }
        },
    ]

    try:
        cursor = mongo.db.knowledge_chunks.aggregate(pipeline)
        results: list[dict] = []
        async for doc in cursor:
            results.append(doc)
        return results
    except Exception as exc:
        print(f"[RAG] Vector search failed: {exc}")
        return []


async def get_simple_rag_chunks(keywords: list[str], top_k: int = 5) -> list[dict[str, Any]]:
    """
    Fallback text-based search when embeddings are not yet set up.
    Uses MongoDB $text search on 'knowledge_chunks' collection.
    Requires a text index on the 'text' field.
    """
    if mongo.db is None:
        return []

    query_text = " ".join(keywords)
    try:
        cursor = (
            mongo.db.knowledge_chunks
            .find({"$text": {"$search": query_text}}, {"score": {"$meta": "textScore"}, "text": 1, "source": 1, "_id": 0})
            .sort([("score", {"$meta": "textScore"})])
            .limit(top_k)
        )
        return [doc async for doc in cursor]
    except Exception as exc:
        print(f"[RAG] Text search failed: {exc}")
        return []


def format_rag_context(chunks: list[dict[str, Any]]) -> str:
    """Format retrieved chunks into a context block for the prompt."""
    if not chunks:
        return ""
    lines = ["--- Relevant Trading Knowledge ---"]
    for i, chunk in enumerate(chunks, 1):
        source = chunk.get("source", "unknown")
        text = chunk.get("text", "")
        lines.append(f"[{i}] ({source}): {text}")
    lines.append("--- End of Knowledge Context ---")
    return "\n".join(lines)
