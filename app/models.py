"""
Database models for the NovaTech AI Sales & Customer Service Agent.

Entities:
  - Category, Product        -> catalog data used by the sales agent / RAG
  - Customer                 -> people who chat with / buy from the store
  - Order, OrderItem         -> the real business action (cart + checkout)
  - Conversation, Message    -> chat history, kept for context + dashboard review
  - Escalation               -> a reply that promised a human follow-up (support queue)
  - KnowledgeItem            -> the RAG knowledge base (FAQs, policies, delivery info...)
"""
from datetime import datetime

from app.extensions import db


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255))

    products = db.relationship("Product", back_populates="category", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Category {self.name}>"


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(40), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"))
    description = db.Column(db.Text, default="")
    specs = db.Column(db.Text, default="")  # free-text specs, kept simple on purpose
    price = db.Column(db.Float, nullable=False, default=0.0)
    stock_quantity = db.Column(db.Integer, nullable=False, default=0)
    image_url = db.Column(db.String(255), default="")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    category = db.relationship("Category", back_populates="products")
    order_items = db.relationship("OrderItem", back_populates="product")

    def in_stock(self):
        return self.stock_quantity > 0

    def to_dict(self):
        return {
            "id": self.id,
            "sku": self.sku,
            "name": self.name,
            "category": self.category.name if self.category else None,
            "description": self.description,
            "specs": self.specs,
            "price": self.price,
            "stock_quantity": self.stock_quantity,
            "in_stock": self.in_stock(),
        }

    def __repr__(self):
        return f"<Product {self.sku} {self.name}>"


class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), default="")
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(40), default="")
    address = db.Column(db.String(255), default="")
    messenger_psid = db.Column(db.String(80), unique=True, nullable=True)  # for the Meta bonus
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    orders = db.relationship("Order", back_populates="customer")
    conversations = db.relationship("Conversation", back_populates="customer")

    def __repr__(self):
        return f"<Customer {self.email}>"


ORDER_STATUSES = ("cart", "pending", "confirmed", "shipped", "cancelled")


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    status = db.Column(db.String(20), default="cart")  # see ORDER_STATUSES
    shipping_address = db.Column(db.String(255), default="")
    total_amount = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = db.relationship("Customer", back_populates="orders")
    items = db.relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

    def recompute_total(self):
        self.total_amount = sum(item.quantity * item.unit_price for item in self.items)
        return self.total_amount

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.status,
            "total_amount": self.total_amount,
            "shipping_address": self.shipping_address,
            "items": [item.to_dict() for item in self.items],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Float, nullable=False)  # snapshot of price at time of adding

    order = db.relationship("Order", back_populates="items")
    product = db.relationship("Product", back_populates="order_items")

    def to_dict(self):
        return {
            "product_id": self.product_id,
            "product_name": self.product.name if self.product else None,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "line_total": self.quantity * self.unit_price,
        }


class Conversation(db.Model):
    __tablename__ = "conversations"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=True)
    channel = db.Column(db.String(20), default="web")  # "web" or "messenger"
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = db.relationship("Customer", back_populates="conversations")
    messages = db.relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )
    escalations = db.relationship(
        "Escalation",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Escalation.created_at",
    )

    @property
    def needs_attention(self) -> bool:
        """True while any hand-off in this conversation is still open."""
        return any(escalation.is_open for escalation in self.escalations)

    def __repr__(self):
        return f"<Conversation {self.id} ({self.channel})>"


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # "user" | "assistant"
    content = db.Column(db.Text, nullable=False)
    intent = db.Column(db.String(20), default="")  # "sales" | "support" (assistant turns only)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    conversation = db.relationship("Conversation", back_populates="messages")


class KnowledgeItem(db.Model):
    """A single chunk of the RAG knowledge base, manageable from the dashboard."""

    __tablename__ = "knowledge_items"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(40), nullable=False, default="faq")  # faq | policy | delivery | general
    title = db.Column(db.String(150), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def searchable_text(self):
        return f"{self.title}\n{self.content}"

    def to_dict(self):
        return {
            "id": self.id,
            "category": self.category,
            "title": self.title,
            "content": self.content,
        }

    def __repr__(self):
        return f"<KnowledgeItem {self.id} {self.title!r}>"


ESCALATION_OPEN = "open"
ESCALATION_HANDLED = "handled"
ESCALATION_STATUSES = (ESCALATION_OPEN, ESCALATION_HANDLED)


class Escalation(db.Model):
    """A reply that promised a human follow-up, queued for the operator.

    Written by the chat / Messenger routes (see app/escalation.py) and resolved
    from the dashboard. `trigger` records how it was detected: the model emitted
    the `[HANDOFF: ...]` marker, or the reply merely *read* like a hand-off.
    """

    __tablename__ = "escalations"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False, index=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=True)
    reason = db.Column(db.Text, default="")  # what the human needs to do
    trigger = db.Column(db.String(20), default="marker")  # "marker" | "phrase"
    status = db.Column(db.String(20), default=ESCALATION_OPEN, index=True)  # see ESCALATION_STATUSES
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    handled_at = db.Column(db.DateTime, nullable=True)
    handled_by = db.Column(db.String(120), default="")
    note = db.Column(db.Text, default="")

    conversation = db.relationship("Conversation", back_populates="escalations")
    message = db.relationship("Message")

    @property
    def is_open(self) -> bool:
        return self.status == ESCALATION_OPEN

    def resolve(self, handled_by: str = "", note: str = "") -> None:
        """Take the hand-off off the queue."""
        self.status = ESCALATION_HANDLED
        self.handled_at = datetime.utcnow()
        self.handled_by = (handled_by or "").strip() or "admin"
        self.note = (note or "").strip()

    def reopen(self) -> None:
        self.status = ESCALATION_OPEN
        self.handled_at = None
        self.handled_by = ""
        self.note = ""

    def to_dict(self):
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "reason": self.reason,
            "trigger": self.trigger,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "handled_at": self.handled_at.isoformat() if self.handled_at else None,
            "handled_by": self.handled_by,
            "note": self.note,
        }

    def __repr__(self):
        return f"<Escalation {self.id} conversation={self.conversation_id} {self.status}>"
