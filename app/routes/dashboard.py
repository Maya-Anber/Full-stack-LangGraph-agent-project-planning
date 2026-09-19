from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import (
    ESCALATION_HANDLED,
    ESCALATION_OPEN,
    ESCALATION_STATUSES,
    Category,
    Conversation,
    Customer,
    Escalation,
    KnowledgeItem,
    Message,
    Order,
    Product,
)
from app.rag.ingest import add_knowledge_item, delete_knowledge_item, update_knowledge_item

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/admin")


def _redirect_target(fallback: str) -> str:
    """Where to send the operator after an action.

    Forms may carry a `next` field so an action taken on one page returns there.
    Only dashboard paths are honoured, so a crafted form field cannot turn this
    into an open redirect.
    """
    target = (request.form.get("next") or "").strip()
    return target if target.startswith("/admin/") else fallback


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------
@dashboard_bp.route("/")
def overview():
    open_escalations = (
        Escalation.query.filter_by(status=ESCALATION_OPEN).order_by(Escalation.created_at.desc()).all()
    )
    stats = {
        "products": Product.query.count(),
        "orders": Order.query.filter(Order.status != "cart").count(),
        "customers": Customer.query.count(),
        "conversations": Conversation.query.count(),
        "knowledge_items": KnowledgeItem.query.count(),
        "needs_attention": len(open_escalations),
    }
    recent_orders = (
        Order.query.filter(Order.status != "cart").order_by(Order.created_at.desc()).limit(5).all()
    )
    recent_conversations = Conversation.query.order_by(Conversation.updated_at.desc()).limit(5).all()
    return render_template(
        "dashboard/index.html",
        stats=stats,
        recent_orders=recent_orders,
        recent_conversations=recent_conversations,
        open_escalations=open_escalations[:5],
    )


# --------------------------------------------------------------------------
# Products
# --------------------------------------------------------------------------
@dashboard_bp.route("/products")
def products():
    items = Product.query.order_by(Product.id.desc()).all()
    return render_template("dashboard/products.html", products=items)


@dashboard_bp.route("/products/add", methods=["GET", "POST"])
def add_product():
    categories = Category.query.order_by(Category.name).all()
    if request.method == "POST":
        f = request.form
        product = Product(
            sku=f["sku"].strip(),
            name=f["name"].strip(),
            category_id=int(f["category_id"]) if f.get("category_id") else None,
            description=f.get("description", ""),
            specs=f.get("specs", ""),
            price=float(f.get("price") or 0),
            stock_quantity=int(f.get("stock_quantity") or 0),
            image_url=f.get("image_url", ""),
        )
        db.session.add(product)
        db.session.commit()
        flash(f"Product '{product.name}' created.", "success")
        return redirect(url_for("dashboard.products"))
    return render_template("dashboard/product_form.html", product=None, categories=categories)


@dashboard_bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    categories = Category.query.order_by(Category.name).all()
    if request.method == "POST":
        f = request.form
        product.sku = f["sku"].strip()
        product.name = f["name"].strip()
        product.category_id = int(f["category_id"]) if f.get("category_id") else None
        product.description = f.get("description", "")
        product.specs = f.get("specs", "")
        product.price = float(f.get("price") or 0)
        product.stock_quantity = int(f.get("stock_quantity") or 0)
        product.image_url = f.get("image_url", "")
        product.is_active = bool(f.get("is_active"))
        db.session.commit()
        flash(f"Product '{product.name}' updated.", "success")
        return redirect(url_for("dashboard.products"))
    return render_template("dashboard/product_form.html", product=product, categories=categories)


@dashboard_bp.route("/products/<int:product_id>/delete", methods=["POST"])
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)

    # Historic orders must stay intact: deleting the row would break the order
    # line items that reference it. The product can still be taken off sale by
    # unticking "Active" in the edit form.
    if product.order_items:
        flash(
            f"'{product.name}' appears in {len(product.order_items)} order line(s), so it was not "
            "deleted. Untick 'Active' instead to stop the agent selling it.",
            "error",
        )
        return redirect(url_for("dashboard.products"))

    db.session.delete(product)
    db.session.commit()
    flash(f"Product '{product.name}' deleted.", "success")
    return redirect(url_for("dashboard.products"))


# --------------------------------------------------------------------------
# Orders (created by the agent's `add_to_cart` / `place_order` tools)
# --------------------------------------------------------------------------
@dashboard_bp.route("/orders")
def orders():
    items = Order.query.filter(Order.status != "cart").order_by(Order.created_at.desc()).all()
    return render_template("dashboard/orders.html", orders=items)


@dashboard_bp.route("/orders/<int:order_id>")
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template("dashboard/order_detail.html", order=order)


@dashboard_bp.route("/orders/<int:order_id>/status", methods=["POST"])
def update_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    order.status = request.form["status"]
    db.session.commit()
    flash(f"Order #{order.id} marked as {order.status}.", "success")
    return redirect(url_for("dashboard.order_detail", order_id=order.id))


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------
@dashboard_bp.route("/customers")
def customers():
    items = Customer.query.order_by(Customer.created_at.desc()).all()
    return render_template("dashboard/customers.html", customers=items)


# --------------------------------------------------------------------------
# Conversations (chat transcripts, for reviewing what the agent said/did)
# --------------------------------------------------------------------------
@dashboard_bp.route("/conversations")
def conversations():
    rows = db.session.query(Escalation.conversation_id, Escalation.status).all()
    attention_ids = {conversation_id for conversation_id, status in rows if status == ESCALATION_OPEN}
    handled_ids = {conversation_id for conversation_id, status in rows if status == ESCALATION_HANDLED}

    only_attention = request.args.get("filter") == "attention"
    query = Conversation.query
    if only_attention:
        # Filter in SQL rather than walking every conversation's escalations.
        query = query.filter(Conversation.id.in_(attention_ids))
    items = query.order_by(Conversation.updated_at.desc()).all()

    return render_template(
        "dashboard/conversations.html",
        conversations=items,
        attention_ids=attention_ids,
        handled_ids=handled_ids - attention_ids,
        active_filter="attention" if only_attention else "all",
    )


@dashboard_bp.route("/conversations/<int:conversation_id>")
def conversation_detail(conversation_id):
    conversation = Conversation.query.get_or_404(conversation_id)
    escalations = conversation.escalations  # chronological, oldest first
    return render_template(
        "dashboard/conversation_detail.html",
        conversation=conversation,
        escalations=escalations,
        escalation_by_message={e.message_id: e for e in escalations if e.message_id},
        open_escalation=next((e for e in escalations if e.is_open), None),
    )


@dashboard_bp.route("/conversations/<int:conversation_id>/reply", methods=["POST"])
def admin_reply(conversation_id: int):
    """The operator types a reply in the dashboard; it is saved as an assistant
    turn so the full thread stays together, and delivered back to the customer.

    Web-chat customers see the new message on their next poll/refresh.
    Messenger customers get a real Graph API message so the live chat is
    end-to-end -- the same support queue the AI escalates into.
    """
    conversation = Conversation.query.get_or_404(conversation_id)
    text = (request.form.get("text") or "").strip()
    if not text:
        flash("Reply cannot be empty.", "error")
        return redirect(url_for("dashboard.conversation_detail", conversation_id=conversation_id))

    message = Message(conversation_id=conversation.id, role="assistant", content=text, intent="support")
    db.session.add(message)
    db.session.commit()

    # Deliver to Messenger customers immediately. Web-chat customers will pick
    # up the new message on their next request, just like an AI reply would.
    if conversation.customer and conversation.channel == "messenger":
        from app.routes.webhook import _send_message

        if conversation.customer.messenger_psid:
            _send_message(conversation.customer.messenger_psid, text)

    flash("Reply sent to the customer.", "success")
    return redirect(_redirect_target(url_for("dashboard.conversation_detail", conversation_id=conversation_id)))


# --------------------------------------------------------------------------
# RAG knowledge base management (view / add / edit / delete)
# --------------------------------------------------------------------------
@dashboard_bp.route("/knowledge")
def knowledge():
    items = KnowledgeItem.query.order_by(KnowledgeItem.category, KnowledgeItem.title).all()
    return render_template("dashboard/knowledge.html", items=items)


@dashboard_bp.route("/knowledge/add", methods=["GET", "POST"])
def add_knowledge():
    if request.method == "POST":
        f = request.form
        add_knowledge_item(f["category"], f["title"].strip(), f["content"].strip())
        flash("Knowledge item added and index refreshed.", "success")
        return redirect(url_for("dashboard.knowledge"))
    return render_template("dashboard/knowledge_form.html", item=None)


@dashboard_bp.route("/knowledge/<int:item_id>/edit", methods=["GET", "POST"])
def edit_knowledge(item_id):
    item = KnowledgeItem.query.get_or_404(item_id)
    if request.method == "POST":
        f = request.form
        update_knowledge_item(item_id, f["category"], f["title"].strip(), f["content"].strip())
        flash("Knowledge item updated and index refreshed.", "success")
        return redirect(url_for("dashboard.knowledge"))
    return render_template("dashboard/knowledge_form.html", item=item)


@dashboard_bp.route("/knowledge/<int:item_id>/delete", methods=["POST"])
def delete_knowledge(item_id):
    delete_knowledge_item(item_id)
    flash("Knowledge item deleted and index refreshed.", "success")
    return redirect(url_for("dashboard.knowledge"))


# --------------------------------------------------------------------------
# Support queue: replies that promised a human follow-up
# --------------------------------------------------------------------------
@dashboard_bp.app_context_processor
def sidebar_counts():
    """Live badge for the sidebar's Support queue link."""
    return {"open_escalation_count": Escalation.query.filter_by(status=ESCALATION_OPEN).count()}


@dashboard_bp.route("/escalations")
def escalations():
    status = request.args.get("status", ESCALATION_OPEN)
    query = Escalation.query
    if status in ESCALATION_STATUSES:
        query = query.filter_by(status=status)
    else:
        status = "all"

    counts = {
        "open": Escalation.query.filter_by(status=ESCALATION_OPEN).count(),
        "handled": Escalation.query.filter_by(status=ESCALATION_HANDLED).count(),
    }
    items = query.order_by(Escalation.created_at.desc()).all()
    return render_template("dashboard/escalations.html", escalations=items, status=status, counts=counts)


@dashboard_bp.route("/escalations/<int:escalation_id>/resolve", methods=["POST"])
def resolve_escalation(escalation_id):
    escalation = Escalation.query.get_or_404(escalation_id)
    escalation.resolve(handled_by=request.form.get("handled_by", ""), note=request.form.get("note", ""))
    db.session.commit()
    flash(f"Conversation #{escalation.conversation_id} marked as handled.", "success")
    return redirect(_redirect_target(url_for("dashboard.escalations")))


@dashboard_bp.route("/escalations/<int:escalation_id>/reopen", methods=["POST"])
def reopen_escalation(escalation_id):
    escalation = Escalation.query.get_or_404(escalation_id)
    escalation.reopen()
    db.session.commit()
    flash(f"Conversation #{escalation.conversation_id} is back in the queue.", "success")
    return redirect(_redirect_target(url_for("dashboard.escalations")))
