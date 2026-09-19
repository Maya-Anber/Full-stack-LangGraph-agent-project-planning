"""
The agent workflow:

                     User Message
                          |
                    classify_intent
                          |
                       retrieve  (RAG lookup, shared by both branches)
                        /    \\
                  "sales"    "support"
                     |            |
               sales_agent   support_agent  (RAG-grounded answer, no tools)
                 |     ^                \\
    (tool_calls?)|     | (loop back after
                 v     |  tool results)         \\
               tools---                          v
                (search_products,            finalize
                 check_product_availability,      |
                 add_to_cart, place_order)        v
                 |                               END
                 +---> (no tool_calls) ---> finalize --> END

This mirrors the suggested architecture from the assessment brief (intent
understanding -> sales / customer-service branch -> RAG/tools -> response),
implemented as a real LangGraph StateGraph with conditional routing and a
ReAct-style tool loop on the sales side.
"""
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.nodes import (
    classify_intent,
    finalize,
    retrieve,
    route_after_sales,
    route_by_intent,
    sales_agent,
    support_agent,
)
from app.agent.state import AgentState
from app.agent.tools import ALL_TOOLS


def build_agent_graph():
    graph = StateGraph(AgentState)

    graph.add_node("classify_intent", classify_intent)
    graph.add_node("retrieve", retrieve)
    graph.add_node("sales_agent", sales_agent)
    graph.add_node("support_agent", support_agent)
    graph.add_node("tools", ToolNode(ALL_TOOLS))
    graph.add_node("finalize", finalize)

    graph.set_entry_point("classify_intent")
    graph.add_edge("classify_intent", "retrieve")

    graph.add_conditional_edges(
        "retrieve",
        route_by_intent,
        {"sales": "sales_agent", "support": "support_agent"},
    )

    graph.add_conditional_edges(
        "sales_agent",
        route_after_sales,
        {"tools": "tools", "end": "finalize"},
    )
    graph.add_edge("tools", "sales_agent")
    graph.add_edge("support_agent", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


# Compiled once at import time and reused across requests.
agent_graph = build_agent_graph()
