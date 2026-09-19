"""
Node functions for the LangGraph agent graph. Each node takes the current
AgentState and returns a partial state update (LangGraph merges it in).
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.llm import get_llm
from app.agent.prompts import CLASSIFIER_SYSTEM_PROMPT, SALES_SYSTEM_PROMPT, SUPPORT_SYSTEM_PROMPT
from app.agent.state import AgentState
from app.agent.tools import ALL_TOOLS
from app.escalation import extract_handoff
from app.rag.retriever import retrieve_context


def _last_human_text(state: AgentState) -> str:
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            return msg.content
    return ""


def _extract_text(message: AIMessage) -> str:
    """AIMessage.content can be a plain string or a list of content blocks
    (e.g. when the model also emitted a tool_use block); normalize to text."""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return str(content)


def classify_intent(state: AgentState) -> dict:
    """Decide whether the latest message belongs to the sales or support flow."""
    llm = get_llm(temperature=0)
    recent = state["messages"][-6:]
    response = llm.invoke([SystemMessage(content=CLASSIFIER_SYSTEM_PROMPT), *recent])
    label = _extract_text(response).strip().lower()
    intent = "sales" if "sales" in label else "support"
    return {"intent": intent}


def retrieve(state: AgentState) -> dict:
    """Pull relevant knowledge-base chunks for the latest user message."""
    query = _last_human_text(state)
    context = retrieve_context(query)
    return {"rag_context": context}


def sales_agent(state: AgentState) -> dict:
    """Sales responder. Has tools bound so it can search products, check
    availability, add to cart, and place orders."""
    llm = get_llm(temperature=0.3).bind_tools(ALL_TOOLS)
    system = SystemMessage(
        content=SALES_SYSTEM_PROMPT.format(
            rag_context=state.get("rag_context") or "(none found)",
            customer_email=state.get("customer_email") or "(unknown - ask the customer)",
        )
    )
    response = llm.invoke([system, *state["messages"]])
    return {"messages": [response]}


def support_agent(state: AgentState) -> dict:
    """Customer service responder. Answers from RAG context only, no tools."""
    llm = get_llm(temperature=0.2)
    system = SystemMessage(content=SUPPORT_SYSTEM_PROMPT.format(rag_context=state.get("rag_context") or "(none found)"))
    response = llm.invoke([system, *state["messages"]])
    return {"messages": [response], "final_response": _extract_text(response)}


def finalize(state: AgentState) -> dict:
    """Pull the final assistant text out of the last AI message in the transcript.

    Also strips the `[HANDOFF: ...]` marker that a hand-off reply ends with (see
    `app/escalation.py`) and reports it to the caller, so the routes can queue the
    conversation for a human -- the customer never sees the marker.
    """
    for msg in reversed(state["messages"]):
        if isinstance(msg, AIMessage):
            clean, reason, trigger = extract_handoff(_extract_text(msg))
            handoff = {"reason": reason, "trigger": trigger} if trigger else None
            return {"final_response": clean, "handoff": handoff}
    return {"final_response": "Sorry, something went wrong generating a response.", "handoff": None}


def route_by_intent(state: AgentState) -> str:
    return state.get("intent", "support")


def route_after_sales(state: AgentState) -> str:
    """If the sales agent's last message requested tool calls, go run them;
    otherwise the reply is ready to finalize."""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return "end"
