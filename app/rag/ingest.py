"""
RAG management: add / update / delete knowledge base entries and keep the
in-memory vector index in sync. This is what the Flask dashboard calls, and
it is also usable directly from a Python shell for scripted seeding.
"""
from flask import current_app

from app.extensions import db
from app.models import KnowledgeItem
from app.rag.vector_store import vector_store


def refresh_index():
    """Rebuild the TF-IDF index from whatever is currently in the database."""
    items = KnowledgeItem.query.all()
    vector_store.build(
        [item.to_dict() for item in items],
        backend=current_app.config.get("RAG_EMBEDDING_BACKEND", "minilm"),
        model_name=current_app.config.get("RAG_EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
    )


def add_knowledge_item(category: str, title: str, content: str) -> KnowledgeItem:
    item = KnowledgeItem(category=category, title=title, content=content)
    db.session.add(item)
    db.session.commit()
    refresh_index()
    return item


def update_knowledge_item(item_id: int, category: str, title: str, content: str) -> KnowledgeItem:
    item = KnowledgeItem.query.get_or_404(item_id)
    item.category = category
    item.title = title
    item.content = content
    db.session.commit()
    refresh_index()
    return item


def delete_knowledge_item(item_id: int) -> None:
    item = KnowledgeItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    refresh_index()
