"""
Tools the sales node can call via LLM tool-calling. Every tool here does a
REAL read or write against the database through the SQLAlchemy models --
nothing here is a fake / hallucinated response.

`add_to_cart` and `place_order` are the required "business action" tools:
they create/mutate real Order and OrderItem rows that show up immediately
in the Flask admin dashboard.
"""
from langchain_core.tools import tool

from app.extensions import db
from app.models import Category, Customer, Order, OrderItem, Product


def _get_or_create_customer(email: str) -> Customer:
    email = (email or "").strip().lower()
    customer = Customer.query.filter_by(email=email).first()
    if not customer:
        customer = Customer(email=email)
        db.session.add(customer)
        db.session.commit()
    return customer


def _get_or_create_cart(customer: Customer) -> Order:
    cart = Order.query.filter_by(customer_id=customer.id, status="cart").first()
    if not cart:
        cart = Order(customer_id=customer.id, status="cart")
        db.session.add(cart)
        db.session.commit()
    return cart


@tool
def search_products(query: str, category: str = "", max_price: float = 0.0) -> str:
    """Search the product catalog by keyword, with optional category and max price filters.

    Args:
        query: Keywords to search for in the product name/description (e.g. "wireless headphones").
        category: Optional category name to filter by (e.g. "Laptops"). Leave empty to search all categories.
        max_price: Optional maximum price filter. Use 0 to mean "no limit".
    """
    q = Product.query.filter(Product.is_active.is_(True))
    if query:
        like = f"%{query}%"
        q = q.filter(db.or_(Product.name.ilike(like), Product.description.ilike(like)))
    if category:
        q = q.join(Category, Product.category_id == Category.id).filter(Category.name.ilike(f"%{category}%"))
    if max_price and max_price > 0:
        q = q.filter(Product.price <= max_price)

    products = q.order_by(Product.price.asc()).limit(8).all()
    if not products:
        return "No matching products were found in the catalog."

    lines = []
    for p in products:
        stock_note = "in stock" if p.in_stock() else "OUT OF STOCK"
        lines.append(f"- {p.name} (SKU {p.sku}) - ${p.price:.2f} - {stock_note} ({p.stock_quantity} units)")
    return "\n".join(lines)


@tool
def check_product_availability(product_name: str) -> str:
    """Check current stock/price for a specific product by (partial) name.

    Args:
        product_name: The product name or a close match, e.g. "AeroBook 14".
    """
    product = Product.query.filter(Product.name.ilike(f"%{product_name}%")).first()
    if not product:
        return f"No product matching '{product_name}' was found."
    if product.in_stock():
        return f"{product.name} is in stock: {product.stock_quantity} units available at ${product.price:.2f}."
    return f"{product.name} is currently OUT OF STOCK (price ${product.price:.2f})."


@tool
def add_to_cart(customer_email: str, product_name: str, quantity: int = 1) -> str:
    """Add a product to the customer's cart. Creates the customer and/or cart if needed.
    This performs a real database write.

    Args:
        customer_email: The customer's email address, used to identify their account/cart.
        product_name: The product name (or close match) to add.
        quantity: How many units to add (default 1).
    """
    if not customer_email:
        return "I need the customer's email address before I can update their cart."

    product = Product.query.filter(Product.name.ilike(f"%{product_name}%")).first()
    if not product:
        return f"Could not find a product matching '{product_name}'."
    if not product.is_active:
        return f"{product.name} is not currently available for purchase."
    if product.stock_quantity < quantity:
        return f"Only {product.stock_quantity} unit(s) of {product.name} are in stock; cannot add {quantity}."

    customer = _get_or_create_customer(customer_email)
    cart = _get_or_create_cart(customer)

    existing_item = next((i for i in cart.items if i.product_id == product.id), None)
    if existing_item:
        existing_item.quantity += quantity
    else:
        cart.items.append(OrderItem(product_id=product.id, quantity=quantity, unit_price=product.price))

    cart.recompute_total()
    db.session.commit()

    return (
        f"Added {quantity} x {product.name} to the cart for {customer_email}. "
        f"Cart #{cart.id} now has {len(cart.items)} line item(s), total ${cart.total_amount:.2f}."
    )


@tool
def place_order(customer_email: str, shipping_address: str = "") -> str:
    """Check out the customer's current cart and turn it into a confirmed order.
    This performs a real database write and is the primary "business action" of this agent.

    Args:
        customer_email: The customer's email address.
        shipping_address: Optional shipping address to attach to the order.
    """
    if not customer_email:
        return "I need the customer's email address before I can place the order."

    customer = Customer.query.filter_by(email=customer_email.strip().lower()).first()
    if not customer:
        return "No cart found for that email -- nothing has been added yet."

    cart = Order.query.filter_by(customer_id=customer.id, status="cart").first()
    if not cart or not cart.items:
        return "The cart is empty, so there is nothing to order yet."

    # Re-validate stock at checkout time and decrement it.
    for item in cart.items:
        if item.product.stock_quantity < item.quantity:
            return (
                f"Cannot complete the order: only {item.product.stock_quantity} unit(s) of "
                f"{item.product.name} remain in stock (cart wants {item.quantity})."
            )

    for item in cart.items:
        item.product.stock_quantity -= item.quantity

    cart.status = "confirmed"
    if shipping_address:
        cart.shipping_address = shipping_address
    cart.recompute_total()
    db.session.commit()

    return (
        f"Order #{cart.id} confirmed for {customer_email}! "
        f"{len(cart.items)} item(s), total ${cart.total_amount:.2f}. "
        "A confirmation would normally be emailed to the customer."
    )


ALL_TOOLS = [search_products, check_product_availability, add_to_cart, place_order]
