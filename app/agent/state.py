"""
The state object threaded through every node of the LangGraph agent.

`messages` uses LangGraph's `add_messages` reducer, so nodes can just return
new messages to append rather than manually managing the list.
"""
from typing import Annotated, List, Optional, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[List, add_messages]

    # Set by the `classify_intent` node, read by the router.
    intent: Optional[str]  # "sales" | "support"

    # Set by the `retrieve` node, read by the sales/support responder nodes.
    rag_context: str

    # Carried through so tool calls can act on the right rows in the DB.
    conversation_id: int
    customer_email: Optional[str]

    # The final natural-language reply, set right before the graph ends.
    final_response: str

    # Set by `finalize` when that reply promised a human follow-up:
    # {"reason": str, "trigger": "marker" | "phrase"}, otherwise None. The routes
    # turn it into an Escalation row for the admin support queue.
    handoff: Optional[dict]
