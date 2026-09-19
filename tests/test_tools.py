"""
Business-action tools: these are the tools the sales node can call, and each one
performs a real read or write against the database.
"""
import pytest

from app.agent.tools import (
    ALL_TOOLS,
    add_to_cart,
    check_product_availability,
    place_order,
    search_products,
)
from app.extensions import db
from app.models import Customer, Order, OrderItem, Product


# ---------------------------------------------------------------------------
# The tool contract exposed to the model
# ---------------------------------------------------------------------------
def test_exactly_the_four_expected_tools_are_exposed_to_the_model():
    assert [tool.name for tool in ALL_TOOLS] == [
        "search_products",
        "check_product_availability",
        "add_to_cart",
        "place_order",
    ]


def test_every_tool_has_a_description_so_the_model_can_choose_it():
    for tool in ALL_TOOLS:
        assert tool.description.strip()


# ---------------------------------------------------------------------------
# search_products
# ---------------------------------------------------------------------------
def test_search_products_matches_on_keyword(app):
    result = search_products.invoke({"query": "earbuds"})

    assert "PulseBuds Pro" in result
    assert "AU-PULSEBUDS" in result


def test_search_products_filters_by_category_and_price(app):
    result = search_products.invoke({"query": "", "category": "Laptops", "max_price": 600})

    assert "EduBook 13" in result
    assert "Forge 16 Pro" not in result  # above the price ceiling


def test_search_products_reports_out_of_stock_products(app):
    result = search_products.invoke({"query": "Nova Phone 12 Mini"})

    assert "OUT OF STOCK" in result


def test_search_products_hides_inactive_products(app):
    product = Product.query.filter_by(sku="AU-BOOM2").one()
    product.is_active = False
    db.session.commit()

    assert "Boom2" not in search_products.invoke({"query": "Boom2"})


def test_search_products_says_so_when_nothing_matches(app):
    assert "No matching products" in search_products.invoke({"query": "quantum teleporter"})


# ---------------------------------------------------------------------------
# check_product_availability
# ---------------------------------------------------------------------------
def test_check_product_availability_reports_in_stock(app):
    result = check_product_availability.invoke({"product_name": "PulseBuds Pro"})

    assert "in stock: 32 units" in result
    assert "$179.00" in result


def test_check_product_availability_reports_out_of_stock(app):
    assert "OUT OF STOCK" in check_product_availability.invoke({"product_name": "Nova Phone 12 Mini"})


def test_check_product_availability_handles_unknown_products(app):
    assert "No product matching" in check_product_availability.invoke({"product_name": "Gibson Les Paul"})


# ---------------------------------------------------------------------------
# add_to_cart (real write)
# ---------------------------------------------------------------------------
def test_add_to_cart_creates_customer_cart_and_line_item(app):
    result = add_to_cart.invoke(
        {"customer_email": "ada@example.com", "product_name": "PulseBuds Pro", "quantity": 2}
    )

    assert "Added 2 x PulseBuds Pro" in result
    assert "$358.00" in result

    customer = Customer.query.filter_by(email="ada@example.com").one()
    cart = Order.query.filter_by(customer_id=customer.id, status="cart").one()
    assert [(item.product.name, item.quantity) for item in cart.items] == [("PulseBuds Pro", 2)]


def test_add_to_cart_does_not_decrement_stock(app):
    """Stock is only committed at checkout, so abandoned carts cannot lose stock."""
    add_to_cart.invoke({"customer_email": "ada@example.com", "product_name": "PulseBuds Pro", "quantity": 3})

    assert Product.query.filter_by(sku="AU-PULSEBUDS").one().stock_quantity == 32


def test_add_to_cart_merges_repeat_additions_of_the_same_product(app):
    add_to_cart.invoke({"customer_email": "ada@example.com", "product_name": "Studio7 Headphones", "quantity": 1})
    add_to_cart.invoke({"customer_email": "ada@example.com", "product_name": "Studio7 Headphones", "quantity": 3})

    customer = Customer.query.filter_by(email="ada@example.com").one()
    cart = Order.query.filter_by(customer_id=customer.id, status="cart").one()
    assert len(cart.items) == 1
    assert cart.items[0].quantity == 4


def test_add_to_cart_keeps_different_products_on_separate_lines(app):
    add_to_cart.invoke({"customer_email": "ada@example.com", "product_name": "Studio7 Headphones"})
    add_to_cart.invoke({"customer_email": "ada@example.com", "product_name": "65W GaN Fast Charger"})

    customer = Customer.query.filter_by(email="ada@example.com").one()
    cart = Order.query.filter_by(customer_id=customer.id, status="cart").one()
    assert len(cart.items) == 2


def test_add_to_cart_refuses_more_than_available_stock(app):
    result = add_to_cart.invoke(
        {"customer_email": "bob@example.com", "product_name": "PulseBuds Pro", "quantity": 99}
    )

    assert "Only 32 unit(s)" in result
    assert Customer.query.filter_by(email="bob@example.com").first() is None
    assert OrderItem.query.count() == 0


def test_add_to_cart_asks_for_an_email_when_it_is_missing(app):
    result = add_to_cart.invoke({"customer_email": "", "product_name": "PulseBuds Pro"})

    assert "email address" in result
    assert Customer.query.count() == 0


def test_add_to_cart_reports_an_unknown_product(app):
    assert "Could not find a product" in add_to_cart.invoke(
        {"customer_email": "ada@example.com", "product_name": "Gibson Les Paul"}
    )


# ---------------------------------------------------------------------------
# place_order (the primary business action)
# ---------------------------------------------------------------------------
def test_place_order_confirms_the_cart_and_decrements_stock(app):
    add_to_cart.invoke({"customer_email": "ada@example.com", "product_name": "Studio7 Headphones", "quantity": 2})

    result = place_order.invoke({"customer_email": "ada@example.com", "shipping_address": "12 Test Street"})

    assert "confirmed" in result
    order = Order.query.filter_by(status="confirmed").one()
    assert order.shipping_address == "12 Test Street"
    assert order.total_amount == pytest.approx(498.00)
    assert Product.query.filter_by(sku="AU-STUDIO7").one().stock_quantity == 16  # 18 - 2


def test_place_order_leaves_no_open_cart_behind(app):
    add_to_cart.invoke({"customer_email": "ada@example.com", "product_name": "Studio7 Headphones"})
    place_order.invoke({"customer_email": "ada@example.com"})

    assert Order.query.filter_by(status="cart").count() == 0

    second_attempt = place_order.invoke({"customer_email": "ada@example.com"})
    assert "cart is empty" in second_attempt


def test_place_order_with_an_empty_cart_explains_there_is_nothing_to_order(app):
    db.session.add(Customer(email="empty@example.com"))
    db.session.commit()

    assert "cart is empty" in place_order.invoke({"customer_email": "empty@example.com"})


def test_place_order_reports_an_unknown_customer(app):
    assert "No cart found" in place_order.invoke({"customer_email": "nobody@example.com"})


def test_place_order_revalidates_stock_at_checkout(app):
    """If stock drops between adding to the cart and checking out, checkout is refused."""
    add_to_cart.invoke({"customer_email": "carl@example.com", "product_name": "Studio7 Headphones", "quantity": 5})

    product = Product.query.filter_by(sku="AU-STUDIO7").one()
    product.stock_quantity = 2  # someone else bought most of them in the meantime
    db.session.commit()

    result = place_order.invoke({"customer_email": "carl@example.com"})

    assert "Cannot complete the order" in result
    assert Order.query.filter_by(status="confirmed").count() == 0
    assert Product.query.filter_by(sku="AU-STUDIO7").one().stock_quantity == 2  # untouched


def test_place_order_is_case_insensitive_about_the_email(app):
    add_to_cart.invoke({"customer_email": "ADA@Example.com", "product_name": "Studio7 Headphones"})

    assert "confirmed" in place_order.invoke({"customer_email": "ada@example.com"})
    assert Customer.query.count() == 1