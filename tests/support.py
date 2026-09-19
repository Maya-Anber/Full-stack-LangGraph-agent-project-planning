"""
Test doubles and helpers used by the whole suite.

`ScriptedChatModel` stands in for `langchain_anthropic.ChatAnthropic`. The agent
nodes only ever call two methods on the chat model -- `bind_tools()` and
`invoke()` -- so a scripted object is enough to drive the *real* LangGraph graph
(intent classification -> retrieval -> branch -> tool loop -> finalize) with no
API key and no network access.

Which script a call should use is decided from the *system prompt* the node
sent, because that is the only thing that distinguishes the classifier, sales,
and support nodes at runtime.
"""
from langchain_core.messages import AIMessage, SystemMessage

# A distinctive phrase from each node's system prompt (see app/agent/prompts.py).
ROLE_MARKERS = (
    ("classifier", "routing brain"),
    ("sales", "sales assistant"),
    ("support", "customer service assistant"),
)


def text_reply(text: str) -> AIMessage:
    """A normal assistant turn: an answer with no tool calls."""
    return AIMessage(content=text)


def tool_call(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    """What ChatAnthropic returns when the model decides to call one tool."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def multi_tool_call(*calls: tuple) -> AIMessage:
    """One assistant turn that requests several tools at once.

    `calls` is a sequence of (tool_name, args, call_id) tuples.
    """
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": cid} for name, args, cid in calls],
    )


class ScriptedChatModel:
    """Returns pre-scripted responses, in order, for each agent role."""

    def __init__(self, classifier=None, sales=None, support=None):
        self._scripts = {
            "classifier": list(classifier or []),
            "sales": list(sales or []),
            "support": list(support or []),
        }
        self.calls = []  # [{"role": ..., "messages": [...]}] in call order
        self.bound_tools = None  # set by bind_tools()

    # ------------------------------------------------------------------
    # The chat-model interface used by app/agent/nodes.py
    # ------------------------------------------------------------------
    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, messages, **kwargs):
        role = self._role(messages)
        self.calls.append({"role": role, "messages": list(messages)})
        script = self._scripts[role]
        if not script:
            raise AssertionError(
                f"the {role} node called the model {len(self.calls_for(role))} times "
                f"but the script only allows {len(self.calls_for(role)) - 1}"
            )
        return script.pop(0)

    # ------------------------------------------------------------------
    # Helpers for assertions
    # ------------------------------------------------------------------
    @staticmethod
    def _role(messages) -> str:
        for message in messages:
            if isinstance(message, SystemMessage):
                text = message.content if isinstance(message.content, str) else str(message.content)
                for role, marker in ROLE_MARKERS:
                    if marker in text:
                        return role
        raise AssertionError("no system prompt found; cannot tell which node called the model")

    def roles(self):
        """Which nodes called the model, in order (e.g. ['classifier', 'sales', 'sales'])."""
        return [call["role"] for call in self.calls]

    def calls_for(self, role: str):
        """The message lists the given node passed in, oldest first."""
        return [call["messages"] for call in self.calls if call["role"] == role]


def make_conversation(channel: str = "web", customer_email: str = None) -> int:
    """Create a Conversation (and its Customer, if an email is given) and return its id.

    Must be called inside a Flask app context.
    """
    from app.extensions import db
    from app.models import Conversation, Customer

    customer = None
    if customer_email:
        customer = Customer.query.filter_by(email=customer_email).first()
        if not customer:
            customer = Customer(email=customer_email)
            db.session.add(customer)
            db.session.commit()

    conversation = Conversation(channel=channel, customer_id=customer.id if customer else None)
    db.session.add(conversation)
    db.session.commit()
    return conversation.id