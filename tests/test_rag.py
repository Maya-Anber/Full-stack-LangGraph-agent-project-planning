"""
RAG layer tests: retrieval quality, the ranking contract, and the promise that
dashboard edits take effect in retrieval immediately.
"""
from app.extensions import db
from app.models import KnowledgeItem
from app.rag.ingest import add_knowledge_item, delete_knowledge_item, update_knowledge_item
from app.rag.retriever import retrieve_context
from app.rag.vector_store import VectorStore


# ---------------------------------------------------------------------------
# Vector store internals (no database, no Flask needed)
# ---------------------------------------------------------------------------
def test_vector_store_ranks_the_most_similar_document_first():
    store = VectorStore()
    store.build(
        [
            {"id": 1, "title": "Return Policy", "content": "Return most items within 30 days for a full refund.", "category": "policy"},
            {"id": 2, "title": "Shipping & Delivery Times", "content": "Standard shipping takes 3-5 business days.", "category": "delivery"},
        ]
    )

    results = store.search("what is your return policy", top_k=2)

    assert [r.id for r in results][0] == 1
    assert results[0].score > results[1].score


def test_vector_store_respects_top_k():
    store = VectorStore()
    store.build(
        [
            {"id": 1, "title": "Return Policy", "content": "Refunds within 30 days of delivery.", "category": "policy"},
            {"id": 2, "title": "Standard Shipping", "content": "Standard shipping takes 3-5 business days.", "category": "delivery"},
            {"id": 3, "title": "Express Shipping", "content": "Express shipping takes 1-2 business days.", "category": "delivery"},
            {"id": 4, "title": "Warranty", "content": "One year of coverage against defects.", "category": "policy"},
        ]
    )

    results = store.search("shipping business days", top_k=2)

    assert len(results) == 2
    assert {r.id for r in results} == {2, 3}  # the two shipping entries, not the others


def test_vector_store_drops_weak_matches_below_min_score():
    store = VectorStore()
    store.build([{"id": 1, "title": "Return Policy", "content": "Returns within 30 days.", "category": "policy"}])

    assert store.search("sourdough starter hydration ratio", top_k=4, min_score=0.2) == []


def test_empty_vector_store_is_safe_to_query():
    store = VectorStore()
    store.build([])

    assert store.is_empty()
    assert store.search("anything at all") == []
    assert store.search("   ") == []


# ---------------------------------------------------------------------------
# Retrieval through the app's knowledge base
# ---------------------------------------------------------------------------
def test_return_policy_question_retrieves_the_return_policy(app):
    context = retrieve_context("Can I return an item if I changed my mind?")

    assert "Return Policy" in context
    assert "30 days" in context


def test_shipping_question_retrieves_delivery_information(app):
    context = retrieve_context("how much does shipping cost and how long does it take?")

    assert "Shipping & Delivery Times" in context
    assert "3-5 business days" in context


def test_student_discount_question_retrieves_the_discount_entry(app):
    context = retrieve_context("do you offer a student discount?")

    assert "Student Discount" in context
    assert "10%" in context


def test_unrelated_question_returns_no_context(app):
    """Nothing in the knowledge base is relevant, so the agent gets no context
    and is expected to say it does not know instead of inventing an answer."""
    assert retrieve_context("how do I bake sourdough bread?") == ""


def test_context_blocks_are_labelled_with_their_category(app):
    context = retrieve_context("what is your warranty policy?")

    assert "[POLICY]" in context
    assert "---" in context


# ---------------------------------------------------------------------------
# Dashboard-driven writes must be visible to retrieval immediately
# ---------------------------------------------------------------------------
def test_new_knowledge_is_searchable_immediately(app):
    add_knowledge_item("delivery", "Drone Delivery Trial", "Same-day drone delivery is being trialled in the city centre.")

    assert "drone delivery" in retrieve_context("is drone delivery available?").lower()


def test_updated_knowledge_replaces_the_old_text_in_retrieval(app):
    item = KnowledgeItem.query.filter_by(title="Return Policy").one()

    update_knowledge_item(
        item.id,
        "policy",
        "Return Policy",
        "Customers may return most items within 14 days of delivery for store credit only.",
    )
    context = retrieve_context("what is the return window?")

    assert "14 days" in context
    assert "30 days" not in context


def test_deleted_knowledge_disappears_from_retrieval(app):
    item = KnowledgeItem.query.filter_by(title="Price Matching").one()
    assert "price match" in retrieve_context("do you price match competitors?").lower()

    delete_knowledge_item(item.id)

    assert KnowledgeItem.query.filter_by(title="Price Matching").first() is None
    assert "price match" not in retrieve_context("do you price match competitors?").lower()


def test_deleting_every_knowledge_item_leaves_retrieval_empty(app):
    for item in KnowledgeItem.query.all():
        delete_knowledge_item(item.id)

    assert retrieve_context("what is your return policy?") == ""
    assert db.session.query(KnowledgeItem).count() == 0