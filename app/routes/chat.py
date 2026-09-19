from flask import Blueprint, current_app, jsonify, render_template, request
from langgraph.errors import GraphRecursionError

from app.agent.runner import run_agent
from app.escalation import record_handoff
from app.extensions import db
from app.models import Conversation, Customer, Escalation, Message

chat_bp = Blueprint("chat", __name__)


@chat_bp.route("/")
def index():
    """A minimal chat widget for demoing the agent without a separate frontend."""
    return render_template("chat_widget.html")


@chat_bp.route("/api/chat", methods=["POST"])
def api_chat():
    payload = request.get_json(silent=True) or {}
    user_text = (payload.get("message") or "").strip()
    conversation_id = payload.get("conversation_id")
    customer_email = (payload.get("customer_email") or "").strip().lower() or None

    if not user_text:
        return jsonify({"error": "message is required"}), 400

    conversation = db.session.get(Conversation, conversation_id) if conversation_id else None

    if conversation is None:
        customer = None
        if customer_email:
            customer = Customer.query.filter_by(email=customer_email).first()
            if not customer:
                customer = Customer(email=customer_email)
                db.session.add(customer)
                db.session.commit()
        conversation = Conversation(channel="web", customer_id=customer.id if customer else None)
        db.session.add(conversation)
        db.session.commit()

    db.session.add(Message(conversation_id=conversation.id, role="user", content=user_text))
    db.session.commit()

    try:
        result = run_agent(conversation.id, user_text, customer_email=customer_email)
    except GraphRecursionError:
        # Checked before RuntimeError on purpose: GraphRecursionError inherits from
        # RecursionError, which is itself a RuntimeError subclass.
        return jsonify({"error": "The assistant took too many steps on that request. Try rephrasing it."}), 500
    except RuntimeError as exc:
        # The LLM isn't configured -- usually a missing provider API key. Hand the
        # widget something it can display instead of a generic 500 error page.
        db.session.rollback()
        return jsonify({"error": str(exc)}), 503
    except Exception:
        # Provider SDK errors are not all RuntimeError subclasses. Keep the API
        # contract JSON so the browser can show a useful failure instead of
        # trying to parse Flask's HTML debugger page.
        db.session.rollback()
        current_app.logger.exception("Agent request failed")
        return jsonify({"error": "The assistant could not complete that request. Check the server log and API key."}), 502

    message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=result["reply"],
        intent=result["intent"],
    )
    db.session.add(message)
    db.session.commit()

    escalation = None

    if result["handoff"]:
        escalation = record_handoff(conversation.id, result["handoff"], message_id=message.id)

    return jsonify(
        {
            "reply": result["reply"],
            "intent": result["intent"],
            "conversation_id": conversation.id,
            "escalated": bool(escalation),
        }
    )
