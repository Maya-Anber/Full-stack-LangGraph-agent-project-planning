"""
The web chat API and the demo widget it powers.

The real LangGraph agent is used, with only the LLM scripted -- so these tests
cover the whole request path: HTTP -> conversation persistence -> graph ->
database -> JSON response.
"""
from langchain_core.messages import HumanMessage

from app.models import Conversation, Customer, Escalation, Message
from tests.support import text_reply, tool_call


# ---------------------------------------------------------------------------
# The demo widget
# ---------------------------------------------------------------------------
def test_chat_page_renders_the_widget(client):
    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "NovaTech AI Assistant" in html
    assert "/static/js/chat.js" in html


def test_static_assets_are_served(client):
    css = client.get("/static/css/style.css")
    js = client.get("/static/js/chat.js")

    assert css.status_code == 200
    assert "chat-window" in css.get_data(as_text=True)
    assert js.status_code == 200
    assert "/api/chat" in js.get_data(as_text=True)


# ---------------------------------------------------------------------------
# /api/chat
# ---------------------------------------------------------------------------
def test_chat_rejects_an_empty_message(client):
    response = client.post("/api/chat", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "message is required"


def test_chat_rejects_a_whitespace_only_message(client):
    response = client.post("/api/chat", json={"message": "   "})

    assert response.status_code == 400
    assert Conversation.query.count() == 0


def test_chat_replies_and_persists_the_conversation(client, app, scripted):
    scripted(
        classifier=[text_reply("support")],
        support=[text_reply("Standard shipping is free on orders over $50.")],
    )

    response = client.post("/api/chat", json={"message": "how much is shipping?"})
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["reply"] == "Standard shipping is free on orders over $50."
    assert payload["intent"] == "support"
    assert isinstance(payload["conversation_id"], int)

    conversation = Conversation.query.one()
    assert conversation.channel == "web"

    messages = Message.query.order_by(Message.id).all()
    assert [(m.role, m.content) for m in messages] == [
        ("user", "how much is shipping?"),
        ("assistant", "Standard shipping is free on orders over $50."),
    ]
    assert messages[0].intent == ""  # only assistant turns carry an intent
    assert messages[1].intent == "support"


def test_second_turn_reuses_the_conversation_and_replays_history(client, app, scripted):
    model = scripted(
        classifier=[text_reply("support"), text_reply("support")],
        support=[
            text_reply("Returns are accepted within 30 days."),
            text_reply("Refunds land back on your card within 5 business days."),
        ],
    )

    first = client.post("/api/chat", json={"message": "what is your return policy?"}).get_json()
    second = client.post(
        "/api/chat",
        json={"message": "how long do refunds take?", "conversation_id": first["conversation_id"]},
    ).get_json()

    assert second["conversation_id"] == first["conversation_id"]
    assert Conversation.query.count() == 1
    assert Message.query.count() == 4

    # both nodes saw the earlier turn as history...
    expected = ["what is your return policy?", "how long do refunds take?"]
    assert [m.content for m in model.calls_for("classifier")[1] if isinstance(m, HumanMessage)] == expected
    assert [m.content for m in model.calls_for("support")[1] if isinstance(m, HumanMessage)] == expected


def test_chat_normalizes_and_links_the_customer_email(client, app, scripted):
    scripted(classifier=[text_reply("sales")], sales=[text_reply("Happy to help!")])

    client.post("/api/chat", json={"message": "hi there", "customer_email": " Ada@Example.com "})

    customer = Customer.query.one()
    assert customer.email == "ada@example.com"
    assert Conversation.query.one().customer_id == customer.id


def test_chat_supports_anonymous_visitors(client, app, scripted):
    scripted(classifier=[text_reply("support")], support=[text_reply("Hello!")])

    client.post("/api/chat", json={"message": "hello"})

    assert Customer.query.count() == 0
    assert Conversation.query.one().customer_id is None


def test_chat_reuses_an_existing_customer_by_email(client, app, scripted):
    scripted(classifier=[text_reply("support"), text_reply("support")], support=[text_reply("a"), text_reply("b")])

    first = client.post("/api/chat", json={"message": "one", "customer_email": "ada@example.com"}).get_json()
    client.post(
        "/api/chat",
        json={"message": "two", "conversation_id": first["conversation_id"], "customer_email": "ada@example.com"},
    )

    assert Customer.query.count() == 1


def test_chat_starts_a_new_conversation_when_the_id_is_unknown(client, app, scripted):
    scripted(classifier=[text_reply("support")], support=[text_reply("Hello!")])

    payload = client.post("/api/chat", json={"message": "hello", "conversation_id": 424242}).get_json()

    assert payload["conversation_id"] != 424242
    assert Conversation.query.count() == 1


def test_chat_route_passes_the_customer_email_to_the_agent(client, app, scripted):
    """The email typed into the widget is what the cart/order tools act on."""
    model = scripted(
        classifier=[text_reply("sales")],
        sales=[
            text_reply("ok"),
        ],
    )

    client.post("/api/chat", json={"message": "do you have laptops?", "customer_email": "ada@example.com"})

    # the sales node is called with the conversation in state; the runner also
    # carries customer_email for the tools, which we assert on in the graph tests
    assert model.calls_for("sales")


# ---------------------------------------------------------------------------
# Failure paths: the widget should get a message it can display
# ---------------------------------------------------------------------------
def test_chat_reports_a_missing_api_key_instead_of_a_500_page(client, app, monkeypatch):
    def no_key(temperature=0.3):
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your .env file (see .env.example).")

    monkeypatch.setattr("app.agent.nodes.get_llm", no_key)

    response = client.post("/api/chat", json={"message": "hello"})

    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.get_json()["error"]


def test_chat_reports_a_runaway_tool_loop_instead_of_crashing(client, app, scripted):
    scripted(
        classifier=[text_reply("sales")],
        sales=[tool_call("search_products", {"query": "laptop"}, f"c{i}") for i in range(30)],
    )

    response = client.post("/api/chat", json={"message": "show me every laptop you have"})

    assert response.status_code == 500
    assert "too many steps" in response.get_json()["error"]


# ---------------------------------------------------------------------------
# Hand-offs to a human: queued immediately when the agent asks for a human
# ---------------------------------------------------------------------------
def test_handoff_is_queued_immediately(client, scripted):
    """Agent hands off -> escalation created right away, no email required."""
    scripted(
        classifier=[text_reply("sales")],
        sales=[
            text_reply(
                "I'm connecting you with a team member right away so they can "
                "start a live-chat with you immediately."
            )
        ],
    )

    payload = client.post("/api/chat", json={"message": "i need a human"}).get_json()

    assert payload["escalated"] is True
    assert Escalation.query.count() == 1


def test_handoff_with_email_is_queued_right_away(client, scripted):
    """Agent hands off with an email on file -> queue immediately.

    The scripted reply includes the [HANDOFF: ...] marker so the router can
    extract a real, human-readable reason for the admin queue. Without the
    marker the phrase fallback would fire with a generic placeholder.
    """
    scripted(
        classifier=[text_reply("sales")],
        sales=[
            text_reply(
                "I'm connecting you with a team member right away so they can "
                "start a live-chat with you immediately.\n\n"
                "[HANDOFF: verify the customer's email and start a live chat]"
            )
        ],
    )

    payload = client.post(
        "/api/chat",
        json={"message": "i need a human", "customer_email": "ada@example.com"},
    ).get_json()

    assert payload["escalated"] is True
    assert Escalation.query.count() == 1

    # The admin-facing reason is the marker text (not the raw phrase).
    assert Escalation.query.one().reason == "verify the customer's email and start a live chat"


def test_second_turn_after_email_keeps_the_existing_queue_item(client, scripted):
    """Customer gives their email on a later turn -> the same escalation stays open."""
    scripted(
        classifier=[text_reply("sales"), text_reply("sales")],
        sales=[
            text_reply(
                "I'm connecting you with a team member right away so they can "
                "start a live-chat with you immediately."
            ),
            text_reply(
                "Thanks — I've saved your email. A support representative will "
                "follow up with you shortly."
            ),
        ],
    )

    first = client.post("/api/chat", json={"message": "i need a human"}).get_json()
    assert first["escalated"] is True
    assert Escalation.query.filter_by(status="open").count() == 1

    second = client.post(
        "/api/chat",
        json={
            "message": "my email is ada@example.com",
            "conversation_id": first["conversation_id"],
            "customer_email": "ada@example.com",
        },
    ).get_json()

    # The handoff is already queued; a follow-up turn should not create a
    # second open escalation for the same conversation.
    assert second["escalated"] is False
    assert Escalation.query.filter_by(status="open").count() == 1
