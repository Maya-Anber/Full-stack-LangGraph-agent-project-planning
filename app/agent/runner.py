"""
Glue between Flask routes and the compiled LangGraph agent: turns DB message
history into LangChain messages, runs the graph, and returns the reply.
"""
from flask import current_app
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import agent_graph
from app.models import Message


def _history_as_messages(conversation_id: int, limit: int):
    rows = (
        Message.query.filter_by(conversation_id=conversation_id)
        .order_by(Message.created_at.asc())
        .all()
    )
    rows = rows[-limit:]
    messages = []
    for row in rows:
        if row.role == "user":
            messages.append(HumanMessage(content=row.content))
        else:
            messages.append(AIMessage(content=row.content))
    return messages


def run_agent(conversation_id: int, user_text: str, customer_email: str = None) -> dict:
    """Run the agent for one user turn.

    Returns {"reply": str, "intent": str, "handoff": dict | None}. `handoff` is
    set when the reply promised a human follow-up; the route that called us is
    responsible for recording it (see app/escalation.py).
    """
    limit = current_app.config.get("MAX_HISTORY_MESSAGES", 12)
    history = _history_as_messages(conversation_id, limit)

    # Routes persist the incoming message before calling in, so the newest row is
    # usually this very turn. Drop it and re-add it below, otherwise the model
    # (and the intent classifier) would see the same message twice. Callers that
    # run the agent without persisting first are unaffected.
    if history and isinstance(history[-1], HumanMessage) and history[-1].content == user_text:
        history = history[:-1]

    state = {
        "messages": history + [HumanMessage(content=user_text)],
        "intent": None,
        "rag_context": "",
        "conversation_id": conversation_id,
        "customer_email": customer_email,
        "final_response": "",
        "handoff": None,
    }

    result = agent_graph.invoke(state, config={"recursion_limit": 25})

    return {
        "reply": result.get("final_response") or "Sorry, I couldn't come up with a response.",
        "intent": result.get("intent") or "support",
        "handoff": result.get("handoff"),
    }
