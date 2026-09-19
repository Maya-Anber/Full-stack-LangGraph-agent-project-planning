
"""Verify the handoff email flow works correctly."""
from app import create_app
from app.config import Config
from app.extensions import db
from app.models import Conversation, Customer, Escalation, Message
from tests.support import ScriptedChatModel, text_reply
import tempfile, pathlib

tmp = pathlib.Path(tempfile.mkdtemp()) / "test.db"


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp}"
    ANTHROPIC_API_KEY = "test-key"
    ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"
    LLM_PROVIDER = "gemini"
    GEMINI_API_KEY = "test-key"
    GEMINI_MODEL = "gemini-2.5-flash"
    RAG_EMBEDDING_BACKEND = "tfidf"


app = create_app(TestConfig)

with app.app_context():
    # Patch the LLM to return a scripted handoff response
    import app.agent.nodes as nodes_mod

    model = ScriptedChatModel(
        support=[text_reply("I will connect you with a human. [HANDOFF: check display availability dates]")]
    )
    nodes_mod.get_llm = lambda temperature=0.3: model

    # Create customer + conversation
    customer = Customer(email="mintizen7@gmail.com")
    db.session.add(customer)
    db.session.commit()

    conversation = Conversation(channel="web", customer_id=customer.id)
    db.session.add(conversation)
    db.session.commit()

    client = app.test_client()

    # Turn 1: User asks for a human
    print("=== Turn 1: User asks for human ===")
    resp = client.post(
        "/api/chat",
        json={
            "message": "connect me with live chat i want to ask more about when will the display be available",
            "conversation_id": conversation.id,
        },
    )
    data = resp.get_json()
    print(f"reply: {data['reply']}")
    print(f"needs_email: {data['needs_email']}")
    print(f"escalated: {data['escalated']}")

    # Turn 2: User provides email
    print("\n=== Turn 2: User provides email ===")
    resp = client.post(
        "/api/chat",
        json={
            "message": "mintizen7@gmail.com",
            "conversation_id": conversation.id,
            "customer_email": "mintizen7@gmail.com",
        },
    )
    data = resp.get_json()
    print(f"reply: {data['reply']}")
    print(f"escalated: {data['escalated']}")

    # Show stored messages
    print("\n=== Stored messages in DB ===")
    messages = (
        Message.query.filter_by(conversation_id=conversation.id)
        .order_by(Message.created_at)
        .all()
    )
    for m in messages:
        print(f"  [{m.role}] {m.content}")

    # Show escalations
    print("\n=== Escalations ===")
    escals = Escalation.query.filter_by(conversation_id=conversation.id).all()
    for e in escals:
        print(f"  reason: {e.reason}")
        print(f"  status: {e.status}")
        print(f"  trigger: {e.trigger}")

    # Verify no "Sorry, I couldn't come up with a response" in any message
    print("\n=== Validation ===")
    bad_messages = [m for m in messages if "Sorry" in m.content and "couldn't come up" in m.content]
    if bad_messages:
        print(f"FAIL: Found {len(bad_messages)} bad 'Sorry, I couldn't come up' messages!")
        for m in bad_messages:
            print(f"  [{m.role}] {m.content}")
    else:
        print("PASS: No 'Sorry, I couldn't come up' messages found.")

    # Verify escalation was created
    if escals and escals[0].status == "open":
        print("PASS: Escalation created and open.")
    else:
        print("FAIL: No open escalation found!")