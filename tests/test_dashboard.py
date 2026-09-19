"""
Admin dashboard: every page renders, and every CRUD flow actually changes what
the agent will see.

The dashboard and the agent share one database, so these tests double as proof
that operator edits (products, knowledge base) reach the agent immediately.
"""
import pytest

from app.agent.tools import add_to_cart, place_order, search_products
from app.escalation import record_handoff
from app.extensions import db
from app.models import Category, Conversation, Customer, Escalation, KnowledgeItem, Message, Order, OrderItem, Product
from app.rag.retriever import retrieve_context
from tests.support import make_conversation, text_reply

ADMIN_PAGES = [
    "/admin/",
    "/admin/products",
    "/admin/products/add",
    "/admin/orders",
    "/admin/customers",
    "/admin/conversations",
    "/admin/knowledge",
    "/admin/knowledge/add",
    "/admin/escalations",
    "/admin/escalations?status=handled",
    "/admin/escalations?status=all",
]


# ---------------------------------------------------------------------------
# Every page renders
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_pages_render(client, path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_pages_use_the_shared_layout(client, path):
    html = client.get(path).get_data(as_text=True)

    assert "NovaTech" in html
    assert "/admin/products" in html  # sidebar navigation


def test_seed_data_is_loaded_on_a_fresh_database(app):
    assert Product.query.count() == 12
    assert Category.query.count() == 5
    assert KnowledgeItem.query.count() == 9


def test_overview_shows_live_counts(client, app):
    html = client.get("/admin/").get_data(as_text=True)

    assert f'<div class="num">{Product.query.count()}</div>' in html
    assert f'<div class="num">{KnowledgeItem.query.count()}</div>' in html


def test_overview_renders_empty_states_on_a_fresh_database(client):
    html = client.get("/admin/").get_data(as_text=True)

    assert "No confirmed orders yet" in html
    assert "No conversations yet" in html


def test_products_page_lists_the_seeded_catalog(client):
    html = client.get("/admin/products").get_data(as_text=True)

    assert "AeroBook 14" in html
    assert "LT-AERO14" in html
    assert "Laptops" in html


def test_products_page_shows_stock_and_price(client):
    html = client.get("/admin/products").get_data(as_text=True)

    assert "$1099.00" in html


def test_customers_and_conversations_pages_render_empty_states(client):
    assert "No customers yet" in client.get("/admin/customers").get_data(as_text=True)
    assert "No conversations yet" in client.get("/admin/conversations").get_data(as_text=True)


def test_orders_page_renders_empty_state(client):
    html = client.get("/admin/orders").get_data(as_text=True)

    assert "No orders placed yet" in html


# ---------------------------------------------------------------------------
# Product CRUD
# ---------------------------------------------------------------------------
PRODUCT_FORM = {
    "sku": "AC-TEST1",
    "name": "Test Charging Cable",
    "category_id": "",
    "description": "A cable.",
    "specs": "1m, USB-C",
    "price": "19.99",
    "stock_quantity": "7",
    "image_url": "",
}


def test_add_product_creates_it_and_confirms(client, app):
    response = client.post("/admin/products/add", data=PRODUCT_FORM, follow_redirects=True)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Test Charging Cable" in html  # the flash message
    assert "created" in html

    product = Product.query.filter_by(sku="AC-TEST1").one()
    assert product.price == pytest.approx(19.99)
    assert product.stock_quantity == 7
    assert product.is_active is True


def test_added_product_is_immediately_sellable_by_the_agent(client, app):
    client.post("/admin/products/add", data=PRODUCT_FORM)

    assert "Test Charging Cable" in search_products.invoke({"query": "Test Charging Cable"})


def test_add_product_form_lists_categories(client):
    html = client.get("/admin/products/add").get_data(as_text=True)

    assert "Smart Home" in html


def test_add_product_links_the_chosen_category(client, app):
    category = Category.query.filter_by(name="Accessories").one()
    data = {**PRODUCT_FORM, "category_id": str(category.id)}

    client.post("/admin/products/add", data=data)

    assert Product.query.filter_by(sku="AC-TEST1").one().category_id == category.id


def test_edit_product_page_is_prefilled(client, app):
    product = Product.query.filter_by(sku="LT-EDU13").one()

    html = client.get(f"/admin/products/{product.id}/edit").get_data(as_text=True)

    assert "EduBook 13" in html
    assert 'value="549.0"' in html


def test_edit_product_updates_its_fields(client, app):
    product = Product.query.filter_by(sku="LT-EDU13").one()
    product_id = product.id

    response = client.post(
        f"/admin/products/{product_id}/edit",
        data={
            "sku": "LT-EDU13",
            "name": "EduBook 13 (2026)",
            "category_id": str(product.category_id),
            "description": "Updated description.",
            "specs": "",
            "price": "499.00",
            "stock_quantity": "30",
            "is_active": "on",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "updated" in response.get_data(as_text=True)

    db.session.expire_all()  # the request committed in its own session
    updated = db.session.get(Product, product_id)
    assert updated.name == "EduBook 13 (2026)"
    assert updated.price == pytest.approx(499.00)
    assert updated.stock_quantity == 30
    assert updated.is_active is True


def test_edit_product_can_take_it_off_the_agent_shelf(client, app):
    """Unticking 'active' hides the product from the sales agent's search tool."""
    product = Product.query.filter_by(sku="LT-EDU13").one()
    product_id = product.id

    client.post(
        f"/admin/products/{product_id}/edit",
        data={
            "sku": "LT-EDU13",
            "name": "EduBook 13",
            "category_id": str(product.category_id),
            "description": "",
            "specs": "",
            "price": "549.00",
            "stock_quantity": "25",
            # no is_active checkbox -> False
        },
    )

    db.session.expire_all()
    assert db.session.get(Product, product_id).is_active is False
    assert "EduBook 13" not in search_products.invoke({"query": "EduBook 13"})


def test_delete_product_removes_it(client, app):
    product = Product.query.filter_by(sku="AU-BOOM2").one()
    product_id = product.id

    response = client.post(f"/admin/products/{product_id}/delete", follow_redirects=True)

    assert response.status_code == 200
    assert "deleted" in response.get_data(as_text=True)
    assert db.session.get(Product, product_id) is None


# ---------------------------------------------------------------------------
# Knowledge base (RAG) CRUD
# ---------------------------------------------------------------------------
def test_knowledge_page_lists_the_seeded_entries(client):
    html = client.get("/admin/knowledge").get_data(as_text=True)

    assert "Return Policy" in html
    assert "Shipping &amp; Delivery Times" in html  # Jinja escapes the ampersand
    assert "policy" in html  # category badge


def test_adding_knowledge_through_the_dashboard_is_searchable_immediately(client, app):
    response = client.post(
        "/admin/knowledge/add",
        data={
            "category": "delivery",
            "title": "Weekend Delivery",
            "content": "Weekend delivery is available for an extra $9.99.",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "index refreshed" in response.get_data(as_text=True)
    assert KnowledgeItem.query.filter_by(title="Weekend Delivery").count() == 1
    assert "Weekend Delivery" in retrieve_context("is weekend delivery available?")


def test_editing_knowledge_through_the_dashboard_updates_retrieval(client, app):
    item = KnowledgeItem.query.filter_by(title="Return Policy").one()
    item_id = item.id

    response = client.post(
        f"/admin/knowledge/{item_id}/edit",
        data={
            "category": "policy",
            "title": "Return Policy",
            "content": "Returns are accepted within 45 days of delivery, in any condition.",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "updated and index refreshed" in response.get_data(as_text=True)

    context = retrieve_context("what is the return policy?")
    assert "45 days" in context
    assert "30 days" not in context  # the old text is gone from the index


def test_knowledge_edit_form_is_prefilled(client, app):
    item = KnowledgeItem.query.filter_by(title="Return Policy").one()

    html = client.get(f"/admin/knowledge/{item.id}/edit").get_data(as_text=True)

    assert "Return Policy" in html
    assert "30 days" in html


def test_deleting_knowledge_through_the_dashboard_removes_it_from_retrieval(client, app):
    item = KnowledgeItem.query.filter_by(title="Student Discount").one()
    item_id = item.id

    response = client.post(f"/admin/knowledge/{item_id}/delete", follow_redirects=True)

    assert response.status_code == 200
    assert "deleted and index refreshed" in response.get_data(as_text=True)
    assert db.session.get(KnowledgeItem, item_id) is None
    assert "Student Discount" not in retrieve_context("do you offer a student discount?")


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
def place_an_order(app, email="ada@example.com", product_name="Studio7 Headphones", quantity=1):
    """Drive the real agent tools to produce an order, then return its id."""
    add_to_cart.invoke({"customer_email": email, "product_name": product_name, "quantity": quantity})
    place_order.invoke({"customer_email": email, "shipping_address": "12 Test Street"})

    return Order.query.filter_by(status="confirmed").order_by(Order.id.desc()).first().id


def test_orders_page_lists_confirmed_orders(client, app):
    order_id = place_an_order(app)

    html = client.get("/admin/orders").get_data(as_text=True)

    assert f"#{order_id}" in html
    assert "ada@example.com" in html
    assert "$249.00" in html
    assert "badge-confirmed" in html


def test_orders_page_hides_abandoned_carts(client, app):
    add_to_cart.invoke({"customer_email": "ada@example.com", "product_name": "Studio7 Headphones"})
    cart_id = Order.query.filter_by(status="cart").one().id

    html = client.get("/admin/orders").get_data(as_text=True)

    assert f"#{cart_id}" not in html
    assert "No orders placed yet" in html


def test_order_detail_shows_line_items_shipping_and_total(client, app):
    order_id = place_an_order(app, quantity=2)

    html = client.get(f"/admin/orders/{order_id}").get_data(as_text=True)

    assert "Studio7 Headphones" in html
    assert "12 Test Street" in html
    assert "$498.00" in html


def test_order_status_can_be_updated(client, app):
    order_id = place_an_order(app)

    response = client.post(f"/admin/orders/{order_id}/status", data={"status": "shipped"}, follow_redirects=True)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "marked as shipped" in html
    assert "badge-shipped" in html

    db.session.expire_all()
    assert db.session.get(Order, order_id).status == "shipped"


def test_agent_placed_order_is_visible_in_the_dashboard(client, app):
    """The whole point of the tools: agent writes show up for the operator."""
    order_id = place_an_order(app, email="grace@example.com", product_name="PulseBuds Pro", quantity=3)

    html = client.get("/admin/orders").get_data(as_text=True)

    assert "grace@example.com" in html
    assert "$537.00" in html  # 3 x $179.00
    assert f"#{order_id}" in html


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
def test_customers_page_lists_customers_with_their_channel(client, app):
    place_an_order(app, email="ada@example.com")
    add_to_cart.invoke({"customer_email": "cart-only@example.com", "product_name": "Boom2 Bluetooth Speaker"})
    db.session.add(Customer(email="messenger-555@placeholder.local", messenger_psid="555"))
    db.session.commit()

    html = client.get("/admin/customers").get_data(as_text=True)

    assert "ada@example.com" in html
    assert "cart-only@example.com" in html  # a cart-only customer is still a customer
    assert "Messenger" in html
    assert "Web" in html


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
def add_transcript(conversation_id, *turns):
    """turns: (role, content) or (role, content, intent)."""
    for turn in turns:
        role, content = turn[0], turn[1]
        intent = turn[2] if len(turn) > 2 else ""
        db.session.add(Message(conversation_id=conversation_id, role=role, content=content, intent=intent))
    db.session.commit()


def test_conversations_page_lists_conversations_with_message_counts(client, app):
    conversation_id = make_conversation("web", "ada@example.com")
    add_transcript(conversation_id, ("user", "hi"), ("assistant", "hello", "support"))

    html = client.get("/admin/conversations").get_data(as_text=True)

    assert f"#{conversation_id}" in html
    assert "ada@example.com" in html
    assert "badge-web" in html


def test_conversation_detail_shows_the_transcript_with_the_handling_agent(client, app):
    conversation_id = make_conversation("messenger", "messenger-555@placeholder.local")
    add_transcript(
        conversation_id,
        ("user", "what is your return policy?"),
        ("assistant", "You can return items within 30 days.", "support"),
    )

    html = client.get(f"/admin/conversations/{conversation_id}").get_data(as_text=True)

    assert "what is your return policy?" in html
    assert "You can return items within 30 days." in html
    assert "support agent" in html
    assert "bubble-user" in html
    assert "bubble-assistant" in html
    assert "badge-messenger" in html


def test_conversation_detail_handles_an_anonymous_conversation(client, app):
    conversation_id = make_conversation("web")

    html = client.get(f"/admin/conversations/{conversation_id}").get_data(as_text=True)

    assert "anonymous" in html


# ---------------------------------------------------------------------------
# Support queue: the admin sees every hand-off and can handle it
# ---------------------------------------------------------------------------
def queue_a_handoff(app, reason="Customer wants a refund approved"):
    conversation_id = make_conversation("web", "ada@example.com")
    db.session.add(Message(conversation_id=conversation_id, role="user", content="i want a refund"))
    db.session.add(
        Message(conversation_id=conversation_id, role="assistant", content="passing this on", intent="support")
    )
    db.session.commit()
    message_id = Message.query.filter_by(conversation_id=conversation_id, role="assistant").one().id
    return record_handoff(
        conversation_id, {"reason": reason, "trigger": "marker"}, message_id=message_id
    ), conversation_id


def test_support_queue_page_lists_the_handoff(client, app):
    escalation, conversation_id = queue_a_handoff(app)

    html = client.get("/admin/escalations").get_data(as_text=True)

    assert "Customer wants a refund approved" in html
    assert f"#{conversation_id}" in html
    assert "ada@example.com" in html
    assert "agent marker" in html


def test_sidebar_and_overview_surface_the_open_count(client, app):
    queue_a_handoff(app)

    sidebar = client.get("/admin/products").get_data(as_text=True)
    assert "Support queue" in sidebar
    assert '<span class="nav-count">1</span>' in sidebar

    overview = client.get("/admin/").get_data(as_text=True)
    assert '<div class="num">1</div>' in overview
    assert "Waiting for a human" in overview


def test_conversations_page_flags_the_attention_conversation(client, app):
    _, conversation_id = queue_a_handoff(app)

    html = client.get("/admin/conversations").get_data(as_text=True)

    assert "needs attention" in html
    assert f"#{conversation_id}" in html

    attention_only = client.get("/admin/conversations?filter=attention").get_data(as_text=True)
    assert f"#{conversation_id}" in attention_only


def test_conversation_detail_shows_the_handoff_banner_and_resolve_action(client, app):
    escalation, conversation_id = queue_a_handoff(app)

    html = client.get(f"/admin/conversations/{conversation_id}").get_data(as_text=True)

    assert "waiting for a human" in html
    assert "Customer wants a refund approved" in html
    assert "handed to a human" in html
    assert f"/admin/escalations/{escalation.id}/resolve" in html


def test_resolving_a_handoff_clears_the_queue(client, app):
    escalation, conversation_id = queue_a_handoff(app)

    response = client.post(
        f"/admin/escalations/{escalation.id}/resolve",
        data={"handled_by": "ops", "note": "called back", "next": "/admin/escalations"},
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "marked as handled" in html
    assert Escalation.query.get(escalation.id).status == "handled"

    queue_html = client.get("/admin/escalations").get_data(as_text=True)
    assert "Nothing waiting" in queue_html

    reopened = client.post(
        f"/admin/escalations/{escalation.id}/reopen",
        data={"next": "/admin/escalations"},
        follow_redirects=True,
    )
    assert "back in the queue" in reopened.get_data(as_text=True)
    assert Escalation.query.get(escalation.id).status == "open"


def test_support_chat_reply_sets_the_escalated_flag(client, app, scripted):
    scripted(classifier=[text_reply("support")], support=[text_reply("passing this to the team, a specialist will reach out")])

    response = client.post(
        "/api/chat", json={"message": "i need a human please", "customer_email": "ada@example.com"}
    )

    assert response.status_code == 200
    assert response.get_json()["escalated"] is True
    assert Escalation.query.count() == 1


# ---------------------------------------------------------------------------
# Admin can join the support queue chat and reply to the customer
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Admin can join the support queue chat and reply to the customer
# ---------------------------------------------------------------------------
def test_admin_reply_saves_into_the_transcript(client, app):
    conversation_id = make_conversation("web", "ada@example.com")

    response = client.post(
        f"/admin/conversations/{conversation_id}/reply",
        data={"text": "Hi Ada, a human is looking into this now.", "next": "/"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Reply sent to the customer." in response.get_data(as_text=True)

    # The admin's message is now part of the transcript.
    saved = Message.query.filter_by(conversation_id=conversation_id, role="assistant").first()
    assert saved is not None
    assert saved.content == "Hi Ada, a human is looking into this now."
    assert saved.intent == "support"


# ---------------------------------------------------------------------------
# Deleting a product that has already been ordered
# ---------------------------------------------------------------------------
def test_deleting_an_ordered_product_keeps_order_history_intact(client, app):
    order_id = place_an_order(app)
    product_id = Product.query.filter_by(sku="AU-STUDIO7").one().id

    response = client.post(f"/admin/products/{product_id}/delete", follow_redirects=True)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "was not deleted" in html  # the operator is told why
    assert db.session.get(Product, product_id) is not None

    order_page = client.get(f"/admin/orders/{order_id}")
    assert order_page.status_code == 200
    assert "Studio7 Headphones" in order_page.get_data(as_text=True)  # history survives