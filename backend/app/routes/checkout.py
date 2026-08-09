"""
Checkout: cart -> Order (+ Razorpay payment) -> on verified payment,
a real DeliveryRecordDB row is created (status=pending, unassigned),
which immediately flows into the existing dispatcher/agent pipeline
completely unchanged, and shows up in the customer's existing "My
Orders" tracking view.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.models.cart import CartItemDB
from app.models.product import ProductDB
from app.models.order import (
    OrderDB, OrderItemDB, OrderStatus,
    CheckoutRequest, CheckoutResponse, VerifyPaymentRequest, OrderOut, OrderItemOut,
)
from app.models.delivery import DeliveryRecordDB, DeliveryStatus
from app.models.customer import CustomerDB
from app.routes.customer_auth import get_current_customer
from app.services.payment import IS_CONFIGURED, RAZORPAY_KEY_ID, create_razorpay_order, verify_razorpay_signature
from app.services.history import record_history_entry
from app.services.notifications import notify_customer_of_status_change

router = APIRouter(prefix="/customer", tags=["checkout"])


@router.post("/checkout", response_model=CheckoutResponse)
def checkout(
    payload: CheckoutRequest,
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    cart_items = db.query(CartItemDB).filter(CartItemDB.customer_id == current_customer.id).all()
    if not cart_items:
        raise HTTPException(status_code=400, detail="Your cart is empty.")

    org_id = cart_items[0].org_id
    subtotal = 0.0
    line_snapshots = []
    for item in cart_items:
        product = db.query(ProductDB).filter(ProductDB.id == item.product_id).first()
        if not product:
            continue
        subtotal += product.price * item.quantity
        line_snapshots.append((product, item.quantity))

    if not line_snapshots:
        raise HTTPException(status_code=400, detail="Every item in your cart is no longer available.")

    subtotal = round(subtotal, 2)
    order = OrderDB(
        id=str(uuid.uuid4()),
        customer_id=current_customer.id,
        org_id=org_id,
        status=OrderStatus.pending_payment,
        address_line=payload.address_line,
        city=payload.city,
        phone=payload.phone,
        subtotal=subtotal,
        created_at=datetime.utcnow(),
    )
    db.add(order)
    db.flush()  # so order.id is usable for the OrderItem rows below, before commit

    for product, quantity in line_snapshots:
        db.add(OrderItemDB(
            id=str(uuid.uuid4()),
            order_id=order.id,
            product_id=product.id,
            product_name=product.name,
            unit_price=product.price,
            quantity=quantity,
        ))

    amount_paise = int(round(subtotal * 100))

    if IS_CONFIGURED:
        razorpay_order = create_razorpay_order(amount_paise=amount_paise, receipt=order.id)
        order.razorpay_order_id = razorpay_order["id"]
        db.commit()
        return CheckoutResponse(
            order_id=order.id,
            razorpay_order_id=razorpay_order["id"],
            razorpay_key_id=RAZORPAY_KEY_ID,
            amount_paise=amount_paise,
            is_test_mode=False,
        )

    # No gateway configured — order + items are still real, saved rows;
    # only the payment step itself is a local stand-in. Clearly flagged
    # both in the DB (is_test_mode_payment) and in this response, so the
    # frontend can visibly label it rather than pretending it's real.
    order.is_test_mode_payment = 1
    db.commit()
    return CheckoutResponse(
        order_id=order.id,
        razorpay_order_id=None,
        razorpay_key_id=None,
        amount_paise=amount_paise,
        is_test_mode=True,
    )


@router.post("/checkout/verify", response_model=OrderOut)
def verify_payment(
    payload: VerifyPaymentRequest,
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    order = db.query(OrderDB).filter(
        OrderDB.id == payload.order_id,
        OrderDB.customer_id == current_customer.id,
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")
    if order.status == OrderStatus.paid:
        raise HTTPException(status_code=400, detail="This order was already paid for.")

    if order.is_test_mode_payment:
        pass  # test-mode order — no signature to verify, proceed straight to fulfillment below
    else:
        if not (payload.razorpay_payment_id and payload.razorpay_order_id and payload.razorpay_signature):
            raise HTTPException(status_code=400, detail="Missing payment verification details.")
        is_valid = verify_razorpay_signature(
            payload.razorpay_order_id, payload.razorpay_payment_id, payload.razorpay_signature
        )
        if not is_valid:
            order.status = OrderStatus.payment_failed
            db.commit()
            raise HTTPException(status_code=400, detail="Payment verification failed. No charge was accepted.")
        order.razorpay_payment_id = payload.razorpay_payment_id

    order.status = OrderStatus.paid

    # Fulfillment: create the actual Delivery, unassigned, for a
    # dispatcher to pick up from their "Unassigned Orders" queue.
    items = db.query(OrderItemDB).filter(OrderItemDB.order_id == order.id).all()
    items_summary = "; ".join(f"{i.quantity}x {i.product_name}" for i in items)
    now = datetime.utcnow()

    delivery = DeliveryRecordDB(
        id=str(uuid.uuid4()),
        agent_id=None,
        order_id=f"ORD-{now.strftime('%y%m%d')}-{order.id[:6].upper()}",
        status=DeliveryStatus.pending,
        notes=f"Items: {items_summary}. Deliver to: {order.address_line}" + (f", {order.city}" if order.city else ""),
        created_at=now,
        updated_at=now,
        org_id=order.org_id,
        customer_email=current_customer.email,
        customer_phone=order.phone,
        customer_id=current_customer.id,
    )
    db.add(delivery)
    order.delivery_id = delivery.id

    # Cart is now spent — clear it.
    db.query(CartItemDB).filter(CartItemDB.customer_id == current_customer.id).delete()

    db.commit()
    db.refresh(order)
    db.refresh(delivery)

    record_history_entry(
        db=db,
        delivery_id=delivery.id,
        changed_by_user_id=current_customer.id,
        changed_by_display_name=current_customer.name,
        old_status=None,
        new_status=DeliveryStatus.pending,
        changed_at=now,
        note="Order placed and paid — awaiting dispatcher assignment",
    )
    notify_customer_of_status_change(
        db,
        delivery_id=delivery.id,
        order_id=delivery.order_id,
        new_status="confirmed",
        customer_email=delivery.customer_email,
        customer_phone=delivery.customer_phone,
        customer_id=delivery.customer_id,
    )

    # Re-query items fresh rather than reusing the pre-commit list above —
    # SQLAlchemy expires all session objects on commit by default, and
    # serializing already-expired instances after the fact is what
    # produced empty {} objects here during testing.
    fresh_items = db.query(OrderItemDB).filter(OrderItemDB.order_id == order.id).all()
    order_out = OrderOut.model_validate(order)
    order_out.items = [OrderItemOut.model_validate(i) for i in fresh_items]
    return order_out


@router.get("/orders", response_model=List[OrderOut])
def list_my_orders(
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    """Itemized purchase history — separate from /customer/deliveries, which tracks fulfillment/shipment status."""
    orders = db.query(OrderDB).filter(OrderDB.customer_id == current_customer.id).order_by(OrderDB.created_at.desc()).all()
    results = []
    for order in orders:
        items = db.query(OrderItemDB).filter(OrderItemDB.order_id == order.id).all()
        order_out = OrderOut.model_validate(order)
        order_out.items = [OrderItemOut.model_validate(i) for i in items]
        results.append(order_out)
    return results
