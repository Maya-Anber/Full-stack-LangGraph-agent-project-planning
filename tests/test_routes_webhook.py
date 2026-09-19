"""
Bonus Meta Messenger integration.

The same LangGraph agent serves Messenger, so these tests cover the webhook
protocol (verification handshake, event parsing) and prove that Messenger
conversations are stored exactly like web ones -- with no outbound HTTP while
no page token is configured.
"""
import pytest

from app.models import Conversation, Customer, Message
from tests.support import text_reply


def _text_event(psid: str, text: str) -> dict:
    return {
        "object": "page",
        "entry": [
            {
                "id": "PAGE-1",
                "time": 1700000000000,
                "messaging": [
                    {
                        "sender": {"id": psid},
                        "recipient": {"id": "PAGE-1"},
                        "timestamp": 1700000000000,
                        "message": {"mid": "mid.1", "text": text},
                    }
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Verification handshake
# ---------------------------------------------------------------------------
def test_webhook_verification_echoes_the_challenge(client):
    response = client.get(
        "/webhook",
        query_string={"hub.mode": "subscribe", "hub.verify_token": "test-verify-token", "hub.challenge": "abc123"},
    )

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "abc123"


def test_webhook_verification_rejects_a_wrong_token(client):
    response = client.get(
        "/webhook",
        query_string={"hub.mode": "subscribe", "hub.verify_token": "not-the-token", "hub.challenge": "abc123"},
    )

    assert response.status_code == 403


def test_webhook_verification_requires_subscribe_mode(client):
    response = client.get(
        "/webhook",
        query_string={"hub.verify_token": "test-verify-token", "hub.challenge": "abc123"},
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Inbound messages
# ---------------------------------------------------------------------------
def test_messenger_message_is_answered_by_the_same_agent(client, app, scripted, monkeypatch):
    monkeypatch.setattr(
        "app.routes.webhook.requests.post",
        lambda *args, **kwargs: pytest.fail("no outbound HTTP expected while no page token is set"),
    )
    scripted(classifier=[text_reply("support")], support=[text_reply("Shipping is free on orders over $50.")])

    response = client.post("/webhook", json=_text_event("PSID-1", "how much is shipping?"))

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}

    customer = Customer.query.one()
    assert customer.messenger_psid == "PSID-1"

    conversation = Conversation.query.one()
    assert conversation.channel == "messenger"
    assert conversation.customer_id == customer.id

    messages = Message.query.order_by(Message.id).all()
    assert [(m.role, m.content) for m in messages] == [
        ("user", "how much is shipping?"),
        ("assistant", "Shipping is free on orders over $50."),
    ]
    assert messages[1].intent == "support"


def test_a_returning_sender_reuses_their_customer_and_conversation(client, app, scripted):
    scripted(
        classifier=[text_reply("support"), text_reply("support")],
        support=[text_reply("Hello!"), text_reply("Yes, still 30 days.")],
    )

    client.post("/webhook", json=_text_event("PSID-1", "hi"))
    client.post("/webhook", json=_text_event("PSID-1", "return window?"))

    assert Customer.query.count() == 1
    assert Conversation.query.count() == 1
    assert Message.query.count() == 4


def test_different_senders_get_separate_conversations(client, app, scripted):
    scripted(
        classifier=[text_reply("support"), text_reply("support")],
        support=[text_reply("Hi A"), text_reply("Hi B")],
    )

    client.post("/webhook", json=_text_event("PSID-A", "hello"))
    client.post("/webhook", json=_text_event("PSID-B", "hello"))

    assert Customer.query.count() == 2
    assert Conversation.query.count() == 2


def test_events_without_text_are_ignored(client, app):
    payload = {
        "object": "page",
        "entry": [{"id": "PAGE-1", "messaging": [{"sender": {"id": "PSID-1"}, "delivery": {"mids": ["mid.1"]}}]}],
    }

    response = client.post("/webhook", json=payload)

    assert response.status_code == 200
    assert Conversation.query.count() == 0
    assert Customer.query.count() == 0


def test_an_empty_payload_is_ignored(client, app):
    assert client.post("/webhook", json={}).status_code == 200
    assert client.post("/webhook", json={"object": "page", "entry": []}).status_code == 200