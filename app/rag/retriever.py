"""
Thin retrieval interface consumed by the LangGraph agent nodes.
Keeps the agent code decoupled from the vector store implementation.
"""
from flask import current_app

from app.rag.vector_store import vector_store


def retrieve_context(query: str) -> str:
    """
    Search the knowledge base and return a formatted context block ready to
    drop into an LLM prompt. Returns an empty string if nothing relevant is
    found, so the agent can fall back to general knowledge / say it doesn't know.
    """
    top_k = current_app.config.get("RAG_TOP_K", 4)
    min_score = current_app.config.get("RAG_MIN_SCORE", 0.05)

    results = vector_store.search(query, top_k=top_k, min_score=min_score)
    if not results:
        return ""

    blocks = []
    for r in results:
        blocks.append(f"[{r.category.upper()}] {r.title}\n{r.content}")
    return "\n\n---\n\n".join(blocks)
