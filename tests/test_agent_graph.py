"""
End-to-end tests for the LangGraph agent with the LLM replaced by a scripted
double.

Everything else is the real thing: the real graph wiring
(classify_intent -> retrieve -> sales/support -> tool loop -> finalize), the
real SQLAlchemy models, and the real business-action tools. Only the model's
text output is faked, which is what lets these tests run without an API key.
"""
import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from app.agent.runner import run_agent
from app.agent.tools import ALL_TOOLS
from app.models import Customer, Order, Product
from tests.support import make_conversation, multi_tool_call, text_reply, tool_call


# ---------------------------------------------------------------------------
# Intent routing
# ---------------------------------------------------------------------------
def test_support_question_is_routed_to_the_support_agent(app, scripted):
    model = scripted(
        classifier=[text_reply("support")],
        support=[text_reply("Standard shipping takes 3-5 business days and is free over $50.")],
    )
    conversation_id = make_conversation()

    result = run_agent(conversation_id, "how long does delivery take?", customer_email=None)

    assert result["intent"] == "support"
    assert result["reply"] == "Standard shipping takes 3-5 business days and is free over $50."
    # the sales branch was never entered, and the support branch binds no tools
    assert model.roles() == ["classifier", "support"]
    assert model.bound_tools is None


def test_sales_question_is_routed_to_the_sales_agent(app, scripted):
    model = scripted(
        classifier=[text_reply("sales")],
        sales=[text_reply("The PulseBuds Pro are $179.00 and in stock.")],
    )
    conversation_id = make_conversation()

    result = run_agent(conversation_id, "do you sell noise cancelling earbuds?", customer_email=None)

    assert result["intent"] == "sales"
    assert result["reply"] == "The PulseBuds Pro are $179.00 and in stock."
    assert model.roles() == ["classifier", "sales"]
    assert model.bound_tools == ALL_TOOLS  # the sales node is the tool-using node


def test_retrieved_knowledge_is_injected_into_the_agent_prompt(app, scripted):
    """The retrieve node runs before the branch, so both agents see RAG context."""
    model = scripted(classifier=[text_reply("support")], support=[text_reply("Returns are accepted within 30 days.")])
    conversation_id = make_conversation()

    run_agent(conversation_id, "what is your return policy?", customer_email=None)

    system_prompt = model.calls_for("support")[0][0].content
    assert "RAG CONTEXT" in system_prompt
    assert "30 days" in system_prompt  # retrieved from the knowledge base


def test_unknown_knowledge_tells_the_agent_there_is_no_context(app, scripted):
    model = scripted(classifier=[text_reply("support")], support=[text_reply("I'm not sure about that.")])
    conversation_id = make_conversation()

    run_agent(conversation_id, "how do I bake sourdough bread?", customer_email=None)

    assert "(none found)" in model.calls_for("support")[0][0].content


def test_classifier_label_is_normalized(app, scripted):
    """The prompt asks for one lowercase word, but the code must not depend on it."""
    scripted(classifier=[text_reply("  Sales.  ")], sales=[text_reply("Sure!")])
    assert run_agent(make_conversation(), "any laptops?", None)["intent"] == "sales"


def test_ambiguous_classifier_output_falls_back_to_support(app, scripted):
    scripted(classifier=[text_reply("greeting")], support=[text_reply("Hi there!")])
    assert run_agent(make_conversation(), "hello", None)["intent"] == "support"


# ---------------------------------------------------------------------------
# The ReAct-style tool loop on the sales branch
# ---------------------------------------------------------------------------
def test_tool_call_loop_executes_the_tool_and_feeds_the_result_back(app, scripted):
    model = scripted(
        classifier=[text_reply("sales")],
        sales=[
            tool_call(
                "add_to_cart",
                {"customer_email": "ada@example.com", "product_name": "PulseBuds Pro", "quantity": 1},
                "call_cart",
            ),
            text_reply("Done - the PulseBuds Pro are in your cart."),
        ],
    )

    result = run_agent(make_conversation(), "add the PulseBuds Pro to my cart, I'm ada@example.com", "ada@example.com")

    assert result["reply"] == "Done - the PulseBuds Pro are in your cart."
    assert model.roles() == ["classifier", "sales", "sales"]  # looped back into sales after the tool ran

    second_sales_call = model.calls_for("sales")[1]
    assert isinstance(second_sales_call[-1], ToolMessage)
    assert "Added 1 x PulseBuds Pro" in second_sales_call[-1].content
    assert all(message.type == "system" for message in [second_sales_call[0]])  # prompt survives the loop

    # ...and the tool wrote a real cart to the database
    customer = Customer.query.filter_by(email="ada@example.com").one()
    cart = Order.query.filter_by(customer_id=customer.id, status="cart").one()
    assert [(item.product.name, item.quantity) for item in cart.items] == [("PulseBuds Pro", 1)]


def test_tool_loop_can_run_several_rounds_to_complete_a_purchase(app, scripted):
    """add_to_cart, then place_order, then the final confirmation."""
    model = scripted(
        classifier=[text_reply("sales")],
        sales=[
            tool_call("add_to_cart", {"customer_email": "ada@example.com", "product_name": "Studio7 Headphones", "quantity": 1}, "call_add"),
            tool_call("place_order", {"customer_email": "ada@example.com", "shipping_address": "1 Demo Way"}, "call_order"),
            text_reply("Order confirmed - thanks for shopping with NovaTech!"),
        ],
    )

    result = run_agent(make_conversation(), "buy the Studio7 Headphones and ship them to 1 Demo Way", "ada@example.com")

    assert result["reply"] == "Order confirmed - thanks for shopping with NovaTech!"
    assert model.roles() == ["classifier", "sales", "sales", "sales"]

    order = Order.query.filter_by(status="confirmed").one()
    assert order.shipping_address == "1 Demo Way"
    assert order.total_amount == pytest.approx(249.00)
    assert Product.query.filter_by(sku="AU-STUDIO7").one().stock_quantity == 17  # 18 - 1


def test_several_tool_calls_in_one_turn_are_all_executed(app, scripted):
    model = scripted(
        classifier=[text_reply("sales")],
        sales=[
            multi_tool_call(
                ("search_products", {"query": "headphones"}, "call_search"),
                ("check_product_availability", {"product_name": "Studio7 Headphones"}, "call_stock"),
            ),
            text_reply("The Studio7 Headphones are $249.00 and in stock."),
        ],
    )

    result = run_agent(make_conversation(), "what headphones do you have?", None)

    assert result["reply"] == "The Studio7 Headphones are $249.00 and in stock."
    tool_results = [message for message in model.calls_for("sales")[1] if isinstance(message, ToolMessage)]
    assert len(tool_results) == 2
    assert "Studio7 Headphones" in tool_results[0].content
    assert "in stock" in tool_results[1].content


def test_a_reply_alone_never_writes_to_the_database(app, scripted):
    """The agent must not claim a business action happened unless a tool ran."""
    scripted(
        classifier=[text_reply("sales")],
        sales=[text_reply("I've placed your order - you're all set!")],
    )

    result = run_agent(make_conversation(), "order the Studio7 Headphones for me", "ada@example.com")

    assert result["reply"].startswith("I've placed")
    assert Order.query.count() == 0
    assert Customer.query.count() == 0


def test_the_graph_can_be_invoked_directly_with_a_state_dict(app, scripted):
    """The graph is usable without the Flask runner, and Anthropic-style content
    blocks (a list, not a string) are normalized into the final response."""
    from langchain_core.messages import AIMessage

    from app.agent.graph import agent_graph

    scripted(
        classifier=[text_reply("sales")],
        sales=[AIMessage(content=[{"type": "text", "text": "Content blocks are normalized."}])],
    )

    result = agent_graph.invoke(
        {
            "messages": [HumanMessage(content="hello")],
            "intent": None,
            "rag_context": "",
            "conversation_id": make_conversation(),
            "customer_email": None,
            "final_response": "",
        },
        config={"recursion_limit": 25},
    )

    assert result["intent"] == "sales"
    assert result["final_response"] == "Content blocks are normalized."


def test_a_model_that_never_stops_calling_tools_is_stopped_by_the_recursion_limit(app, scripted):
    from langgraph.errors import GraphRecursionError

    scripted(
        classifier=[text_reply("sales")],
        sales=[tool_call("search_products", {"query": "laptop"}, f"call_{i}") for i in range(30)],
    )

    with pytest.raises(GraphRecursionError):
        run_agent(make_conversation(), "show me every laptop you have", None)