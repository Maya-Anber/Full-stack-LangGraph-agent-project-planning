"""
Bonus integration: Facebook Page / Meta Messenger.

Flow: Messenger -> Meta webhook -> this Flask route -> same LangGraph agent
used by the web chat -> reply sent back via the Graph API.

To use this for real you need:
  1. A Meta developer app with Messenger product added, subscribed to a Page.
  2. FB_VERIFY_TOKEN and FB_PAGE_ACCESS_TOKEN set in your environment.
  3. This route publicly reachable over HTTPS (e.g. via ngrok in development)
     and registered as the app's webhook callback URL.

This is not required by the assessment but demonstrates that the same agent
can be reused across channels without any changes to the LangGraph graph.
"""
import logging
import requests
from flask import Blueprint, current_app, jsonify, request

from app.agent.runner import run_agent
from app.escalation import record_handoff
from app.extensions import db
from app.models import Conversation, Customer, Message

logger = logging.getLogger(__name__)

webhook_bp = Blueprint("webhook", __name__)

GRAPH_API_URL = "https://graph.facebook.com/v19.0/me/messages"


@webhook_bp.route("/webhook", methods=["GET"])
def verify():
    """Meta calls this once, synchronously, to verify webhook ownership."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    logger.info("Webhook verification attempt: mode=%s token_set=%s", mode, bool(token))

    if mode == "subscribe" and token == current_app.config["FB_VERIFY_TOKEN"]:
        logger.info("Webhook verification successful")
        return challenge, 200
    logger.warning("Webhook verification failed: mode=%s", mode)
    return "Verification failed", 403


@webhook_bp.route("/webhook", methods=["POST"])
def receive():
    data = request.get_json(silent=True) or {}

    logger.info("Webhook POST received: object=%s entries=%d", data.get("object"), len(data.get("entry", [])))

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            sender_psid = event.get("sender", {}).get("id")
            text = event.get("message", {}).get("text")
            if not sender_psid or not text:
                logger.debug("Ignoring non-message event from sender %s: %s", sender_psid or "unknown", list(event.keys()))
                continue  # ignore delivery receipts, postbacks, etc.

            logger.info("Processing message from PSID=%s: %r", sender_psid, text[:100])
            reply = _handle_message(sender_psid, text)
            _send_message(sender_psid, reply)

    return jsonify({"status": "ok"})


def _handle_message(sender_psid: str, text: str) -> str:
    customer = Customer.query.filter_by(messenger_psid=sender_psid).first()
    if not customer:
        # Messenger doesn't give us an email; use a placeholder tied to the PSID
        # so the same cart/order tools still work. A real deployment would
        # collect the email during checkout instead. Lowercased, because the
        # tools and the web chat both normalise emails when looking a customer
        # up -- otherwise the same person would end up with two accounts.
        email = f"messenger-{sender_psid}@placeholder.local".lower()
        customer = Customer(email=email, messenger_psid=sender_psid)
        db.session.add(customer)
        db.session.commit()

    conversation = Conversation.query.filter_by(customer_id=customer.id, channel="messenger").first()
    if not conversation:
        conversation = Conversation(customer_id=customer.id, channel="messenger")
        db.session.add(conversation)
        db.session.commit()

    db.session.add(Message(conversation_id=conversation.id, role="user", content=text))
    db.session.commit()

    result = run_agent(conversation.id, text, customer_email=customer.email)

    message = Message(
        conversation_id=conversation.id, role="assistant", content=result["reply"], intent=result["intent"]
    )
    db.session.add(message)
    db.session.commit()

    # Messenger conversations land in the same support queue as web ones.
    # Messenger customers always have a Customer row (created above), so the
    # email-gating logic in chat.py doesn't apply here -- we queue directly.
    if result["handoff"]:
        record_handoff(conversation.id, result["handoff"], message_id=message.id)

    return result["reply"]


def _send_message(recipient_psid: str, text: str) -> None:
    token = current_app.config["FB_PAGE_ACCESS_TOKEN"]
    if not token:
        logger.warning("FB_PAGE_ACCESS_TOKEN not set; skipping outbound Messenger send.")
        return
    logger.info("Sending Messenger reply to PSID=%s: %r", recipient_psid, text[:200])
    try:
        resp = requests.post(
            GRAPH_API_URL,
            params={"access_token": token},
            json={"recipient": {"id": recipient_psid}, "message": {"text": text}},
            timeout=10,
        )
        logger.info("Graph API response: status=%d body=%s", resp.status_code, resp.text[:500])
        if resp.status_code != 200:
            logger.error("Graph API rejected send: %s", resp.text)
    except requests.RequestException as exc:
        logger.error("Failed to send Messenger reply: %s", exc)
