"""
Populates the database with a small, realistic electronics catalog and a
starter RAG knowledge base, so the app is immediately demoable after
`flask run` on a fresh database. Safe to call multiple times -- it only
seeds when the relevant tables are empty.
"""
from app.extensions import db
from app.models import Category, KnowledgeItem, Product

CATEGORIES = ["Laptops", "Smartphones", "Audio", "Accessories", "Smart Home"]

PRODUCTS = [
    dict(sku="LT-AERO14", name="AeroBook 14", category="Laptops", price=1099.00, stock=14,
         description="14-inch ultralight laptop for everyday work and travel.",
         specs="13th-gen CPU, 16GB RAM, 512GB SSD, 1.1kg, 18h battery life"),
    dict(sku="LT-FORGE16", name="Forge 16 Pro", category="Laptops", price=1899.00, stock=6,
         description="High-performance 16-inch laptop for creators and gamers.",
         specs="14-core CPU, 32GB RAM, 1TB SSD, discrete GPU, 165Hz display"),
    dict(sku="LT-EDU13", name="EduBook 13", category="Laptops", price=549.00, stock=25,
         description="Affordable, durable laptop for students.",
         specs="8-core CPU, 8GB RAM, 256GB SSD, spill-resistant keyboard"),
    dict(sku="PH-NOVA12", name="Nova Phone 12", category="Smartphones", price=799.00, stock=20,
         description="Flagship smartphone with a triple-camera system.",
         specs="6.5in OLED, 256GB storage, 5G, 48MP main camera, 2-day battery"),
    dict(sku="PH-NOVA12-MINI", name="Nova Phone 12 Mini", category="Smartphones", price=649.00, stock=0,
         description="Compact version of the Nova Phone 12, same chipset in a smaller body.",
         specs="6.0in OLED, 128GB storage, 5G, dual camera"),
    dict(sku="PH-BUDGET5", name="ValuePhone 5", category="Smartphones", price=249.00, stock=40,
         description="Reliable budget smartphone for everyday use.",
         specs="6.1in LCD, 64GB storage, 4G, fingerprint sensor"),
    dict(sku="AU-PULSEBUDS", name="PulseBuds Pro", category="Audio", price=179.00, stock=32,
         description="Noise-cancelling true wireless earbuds.",
         specs="Active noise cancellation, 30h total battery with case, IPX4"),
    dict(sku="AU-STUDIO7", name="Studio7 Headphones", category="Audio", price=249.00, stock=18,
         description="Over-ear studio headphones with rich, balanced sound.",
         specs="40mm drivers, wired + Bluetooth, 35h battery"),
    dict(sku="AU-BOOM2", name="Boom2 Bluetooth Speaker", category="Audio", price=89.00, stock=50,
         description="Compact waterproof speaker for indoor and outdoor use.",
         specs="360-degree sound, IP67 waterproof, 12h battery"),
    dict(sku="AC-CHARGE65", name="65W GaN Fast Charger", category="Accessories", price=39.00, stock=100,
         description="Compact fast charger compatible with laptops and phones.",
         specs="USB-C PD 65W, GaN technology, foldable plug"),
    dict(sku="AC-CASE-NOVA12", name="Nova Phone 12 Protective Case", category="Accessories", price=24.00, stock=60,
         description="Shock-absorbing case designed for the Nova Phone 12.",
         specs="Military-grade drop protection, wireless-charging compatible"),
    dict(sku="SH-HUB1", name="HomeHub Smart Speaker", category="Smart Home", price=99.00, stock=22,
         description="Voice-controlled smart speaker and home hub.",
         specs="Built-in voice assistant, Zigbee hub, multi-room audio"),
]

KNOWLEDGE_ITEMS = [
    dict(category="policy", title="Return Policy",
         content="Customers may return most items within 30 days of delivery for a full refund, "
                 "provided the product is in its original condition and packaging. Opened software, "
                 "gift cards, and final-sale clearance items are not eligible for return. Refunds are "
                 "issued to the original payment method within 5-7 business days of us receiving the item."),
    dict(category="policy", title="Warranty Coverage",
         content="All NovaTech products come with a minimum 1-year manufacturer warranty covering "
                 "defects in materials and workmanship. Laptops in the 'Pro' line include a 2-year "
                 "warranty. Warranty does not cover accidental damage, liquid damage, or unauthorized "
                 "repairs. Contact support with your order number to start a warranty claim."),
    dict(category="delivery", title="Shipping & Delivery Times",
         content="Standard shipping takes 3-5 business days and is free on orders over $50 (otherwise "
                 "a flat $6.99 fee applies). Express shipping (1-2 business days) is available at "
                 "checkout for an additional $14.99. We currently ship within the country only; "
                 "international shipping is not yet supported."),
    dict(category="delivery", title="Order Tracking",
         content="Once an order ships, the customer receives an email with a tracking link. Orders can "
                 "also be checked anytime by contacting support with the order number. Orders typically "
                 "leave our warehouse within 1 business day of being placed."),
    dict(category="faq", title="Payment Methods",
         content="We accept all major credit and debit cards, as well as PayPal. Payment is captured "
                 "at the time the order is placed. We do not currently support buy-now-pay-later "
                 "services or cryptocurrency."),
    dict(category="faq", title="Price Matching",
         content="NovaTech offers price matching against major authorized retailers for identical, "
                 "in-stock items, within 7 days of purchase. Marketplace sellers, clearance, and "
                 "flash-sale prices are excluded. Customers can request a price match by contacting "
                 "support with a link to the competitor listing."),
    dict(category="faq", title="Student Discount",
         content="Verified students receive 10% off laptops in the EduBook line through our student "
                 "verification partner. The discount cannot be combined with other promotions and is "
                 "limited to one redemption per customer per year."),
    dict(category="general", title="About NovaTech",
         content="NovaTech is an online electronics retailer specializing in laptops, smartphones, "
                 "audio gear, smart home devices, and accessories. We focus on curating a smaller, "
                 "well-tested catalog rather than carrying every brand, so customers can shop with "
                 "confidence. NovaTech was founded with a mission to make good technology decisions "
                 "simple."),
    dict(category="faq", title="Business Hours & Human Support",
         content="Our human support team is available Monday-Friday, 9am-6pm (store timezone), via "
                 "email at support@novatech.example and live chat during those hours. The AI assistant "
                 "is available 24/7 for product questions, order help, and placing orders."),
]


def seed_if_empty():
    if Category.query.count() == 0:
        cat_objs = {name: Category(name=name) for name in CATEGORIES}
        db.session.add_all(cat_objs.values())
        db.session.commit()

        for p in PRODUCTS:
            db.session.add(
                Product(
                    sku=p["sku"],
                    name=p["name"],
                    category_id=cat_objs[p["category"]].id,
                    description=p["description"],
                    specs=p["specs"],
                    price=p["price"],
                    stock_quantity=p["stock"],
                )
            )
        db.session.commit()

    if KnowledgeItem.query.count() == 0:
        for k in KNOWLEDGE_ITEMS:
            db.session.add(KnowledgeItem(category=k["category"], title=k["title"], content=k["content"]))
        db.session.commit()
