"""
Full-stack tests: HTTP chat -> LangGraph agent -> business-action tool ->
database -> admin dashboard.

These are the "does the whole thing actually work together" tests. Only the LLM
is scripted; every other layer is the real one.
"""
from langchain_core.messages import ToolMessage

from app.models import Conversation, Customer, KnowledgeItem, Message, Order, Product
from tests.support import text_reply, tool_call


def test_a_customer_can_browse_and_place_a_real_order_through_the_chat(client, app, scripted):
    scripted(
        classifier=[text_reply("sales")],
        sales=[
            tool_call("search_products", {"query": "headphones"}, "c1"),
            tool_call("check_product_availability", {"product_name": "Studio7 Headphones"}, "c2"),
            tool_call(
                "add_to_cart",
                {"customer_email": "ada@example.com", "product_name": "Studio7 Headphones", "quantity": 1},
                "c3",
            ),
            tool_call("place_order", {"customer_email": "ada@example.com", "shipping_address": "12 Test Street"}, "c4"),
            text_reply("Your order is confirmed - it should arrive in 3-5 business days."),
        ],
    )

    payload = client.post(
        "/api/chat",
        json={"message": "I'd like to buy the Studio7 Headphones", "customer_email": "ada@example.com"},
    ).get_json()

    assert payload["intent"] == "sales"
    assert payload["reply"].startswith("Your order is confirmed")

    # 1. the tools wrote real rows
    order = Order.query.filter_by(status="confirmed").one()
    assert order.total_amount == 249.00
    assert order.shipping_address == "12 Test Street"
    assert Product.query.filter_by(sku="AU-STUDIO7").one().stock_quantity == 17

    # 2. the operator sees the order
    orders_html = client.get("/admin/orders").get_data(as_text=True)
    assert "ada@example.com" in orders_html
    assert f"#{order.id}" in orders_html

    detail_html = client.get(f"/admin/orders/{order.id}").get_data(as_text=True)
    assert "Studio7 Headphones" in detail_html
    assert "12 Test Street" in detail_html

    # 3. and the transcript records which agent handled it
    transcript = client.get(f"/admin/conversations/{payload['conversation_id']}").get_data(as_text=True)
    assert "like to buy the Studio7 Headphones" in transcript  # Jinja escapes the apostrophe
    assert "sales agent" in transcript
    assert "Your order is confirmed" in transcript


def test_the_customers_email_reaches_the_sales_agent(client, app, scripted):
    """The email typed into the widget must be usable by the cart/order tools
    without the customer having to repeat it in the conversation."""
    model = scripted(classifier=[text_reply("sales")], sales=[text_reply("Sure!")])

    client.post("/api/chat", json={"message": "do you have headphones?", "customer_email": "ada@example.com"})

    assert "ada@example.com" in model.calls_for("sales")[0][0].content


def test_a_product_deactivated_in_the_dashboard_is_no_longer_sellable(client, app, scripted):
    product = Product.query.filter_by(sku="AU-STUDIO7").one()

    client.post(
        f"/admin/products/{product.id}/edit",
        data={
            "sku": product.sku,
            "name": product.name,
            "category_id": str(product.category_id),
            "description": product.description,
            "specs": product.specs,
            "price": str(product.price),
            "stock_quantity": str(product.stock_quantity),
            # is_active intentionally omitted -> the product is taken off the shelf
        },
    )

    model = scripted(
        classifier=[text_reply("sales")],
        sales=[
            tool_call("add_to_cart", {"customer_email": "ada@example.com", "product_name": "Studio7 Headphones"}, "c1"),
            text_reply("That model isn't available at the moment - can I suggest an alternative?"),
        ],
    )

    client.post(
        "/api/chat",
        json={"message": "add the Studio7 Headphones to my cart", "customer_email": "ada@example.com"},
    )

    tool_result = [message for message in model.calls_for("sales")[1] if isinstance(message, ToolMessage)][-1]
    assert "not currently available" in tool_result.content
    assert Order.query.count() == 0


def test_a_sale_completed_over_messenger_shows_up_in_the_same_dashboard(client, app, scripted):
    # The webhook stores Messenger users under a placeholder email tied to their
    # PSID (lowercased, like every other email in the system).
    placeholder_email = "messenger-psid-777@placeholder.local"
    scripted(
        classifier=[text_reply("sales")],
        sales=[
            tool_call("add_to_cart", {"customer_email": placeholder_email, "product_name": "Boom2 Bluetooth Speaker", "quantity": 2}, "c1"),
            tool_call("place_order", {"customer_email": placeholder_email}, "c2"),
            text_reply("Order placed!"),
        ],
    )

    event = {
        "object": "page",
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "PSID-777"},
                        "recipient": {"id": "PAGE-1"},
                        "message": {"mid": "mid.1", "text": "I'll take two Boom2 speakers"},
                    }
                ]
            }
        ],
    }
    client.post("/webhook", json=event)

    order = Order.query.filter_by(status="confirmed").one()
    assert order.total_amount == 178.00  # 2 x $89.00

    assert Conversation.query.one().channel == "messenger"
    assert Message.query.count() == 2

    orders_html = client.get("/admin/orders").get_data(as_text=True)
    assert placeholder_email in orders_html



def test_a_support_question_never_touches_the_catalog_or_orders(client, app, scripted):
    scripted(
        classifier=[text_reply("support")],
        support=[text_reply("You have 30 days from delivery to return an item.")],
    )

    payload = client.post("/api/chat", json={"message": "can I return something?"}).get_json()

    assert payload["intent"] == "support"
    assert Order.query.count() == 0
    assert Customer.query.count() == 0

    transcript = client.get(f"/admin/conversations/{payload['conversation_id']}").get_data(as_text=True)
    assert "support agent" in transcript
    assert "You have 30 days from delivery" in transcript


def test_the_agents_grounding_follows_dashboard_edits_to_the_knowledge_base(client, app, scripted):
    """Edit a policy in the dashboard and the very next reply is grounded in it."""
    item = KnowledgeItem.query.filter_by(title="Return Policy").one()

    client.post(
        f"/admin/knowledge/{item.id}/edit",
        data={
            "category": "policy",
            "title": "Return Policy",
            "content": "Returns are accepted within 45 days of delivery, for any reason.",
        },
    )

    model = scripted(classifier=[text_reply("support")], support=[text_reply("You have 45 days.")])

    client.post("/api/chat", json={"message": "what is your return policy?"})

    grounding = model.calls_for("support")[0][0].content
    assert "45 days" in grounding
    assert "30 days" not in grounding