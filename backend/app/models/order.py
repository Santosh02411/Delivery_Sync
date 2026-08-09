"""
OrderDB / OrderItemDB: the checkout record — what was bought, at what
price (snapshotted at purchase time, so later price changes never alter
a past receipt), and its payment status.

Deliberately kept separate from DeliveryRecordDB: an Order is "what was
purchased and whether it was paid for"; a Delivery is "the physical
fulfillment/shipment of it". On successful payment, checkout.py creates
ONE DeliveryRecordDB row from this order (see routes/checkout.py) and
links back to it via delivery_id — from that point on, the existing
dispatcher/agent pipeline (unchanged) handles getting it to the
customer's door, and it shows up in the customer's existing "My Orders"
tracking view exactly like any other delivery.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Float, Integer, Enum as SqlEnum
from pydantic import BaseModel
from typing import Optional, List

from app.db.session import Base


class OrderStatus(str, enum.Enum):
    pending_payment = "pending_payment"
    paid = "paid"
    payment_failed = "payment_failed"


class OrderDB(Base):
    __tablename__ = "orders"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id = Column(String, index=True, nullable=False)
    org_id = Column(String, index=True, nullable=False)
    status = Column(SqlEnum(OrderStatus), nullable=False, default=OrderStatus.pending_payment)

    address_line = Column(String, nullable=False)
    city = Column(String, nullable=True)
    phone = Column(String, nullable=False)

    subtotal = Column(Float, nullable=False)

    # Real Razorpay identifiers — set once a payment attempt/order is
    # actually created with Razorpay. See services/payment.py.
    razorpay_order_id = Column(String, nullable=True)
    razorpay_payment_id = Column(String, nullable=True)

    # True only when no Razorpay keys are configured and this order went
    # through the clearly-labeled fallback path instead of a real
    # gateway — see services/payment.py's module docstring for why this
    # exists and how it's surfaced to the customer.
    is_test_mode_payment = Column(Integer, nullable=False, default=0)  # 0/1 as SQLite has no native bool constraint issues here

    delivery_id = Column(String, nullable=True)  # set once payment is verified and a Delivery is created
    created_at = Column(DateTime, nullable=False)


class OrderItemDB(Base):
    __tablename__ = "order_items"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id = Column(String, index=True, nullable=False)
    product_id = Column(String, nullable=False)
    product_name = Column(String, nullable=False)   # snapshotted at purchase time
    unit_price = Column(Float, nullable=False)       # snapshotted at purchase time
    quantity = Column(Integer, nullable=False)


class CheckoutRequest(BaseModel):
    address_line: str
    city: Optional[str] = None
    phone: str


class OrderItemOut(BaseModel):
    product_id: str
    product_name: str
    unit_price: float
    quantity: int

    class Config:
        from_attributes = True


class OrderOut(BaseModel):
    id: str
    status: OrderStatus
    address_line: str
    city: Optional[str] = None
    phone: str
    subtotal: float
    is_test_mode_payment: bool
    delivery_id: Optional[str] = None
    created_at: datetime
    items: List[OrderItemOut] = []

    class Config:
        from_attributes = True


class CheckoutResponse(BaseModel):
    """
    What the frontend needs to either launch Razorpay's real Checkout.js
    widget (when a real gateway is configured) or show the clearly-
    labeled test-mode confirmation flow (when it isn't).
    """
    order_id: str
    razorpay_order_id: Optional[str] = None
    razorpay_key_id: Optional[str] = None
    amount_paise: int
    currency: str = "INR"
    is_test_mode: bool


class VerifyPaymentRequest(BaseModel):
    order_id: str
    razorpay_payment_id: Optional[str] = None
    razorpay_order_id: Optional[str] = None
    razorpay_signature: Optional[str] = None
