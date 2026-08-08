"""
Sends a customer notification when a delivery's status changes, via
whichever contact channel(s) are actually on file for that delivery.
Neither, either, or both of email/SMS may fire — nothing is required.

Kept as its own thin module (rather than calling email.py/sms.py
directly from the route/conflict-resolver code) so the call site only
needs to know "notify about this status change" — not which channels
exist or how each one is contacted.

A notification failure (bad SMTP config, Twilio error, etc.) is caught
and logged here rather than allowed to propagate — sending a customer
notification is a nice-to-have side effect of a status update, and must
never be the reason the actual status update itself fails.
"""

"""
Sends a customer notification when a delivery's status changes, via
whichever channel(s) actually apply:
1. In-app notification (CustomerNotificationDB) — the REAL, primary
   channel: if this delivery is linked to a registered customer account
   (customer_id set), they see this the moment they open their
   dashboard, no email/phone/server access needed.
2. Email/SMS — a secondary, best-effort external channel for reaching
   someone even when they're not logged into the dashboard right now
   (or have no account at all). Uses the console-log-by-default pattern
   documented in email.py/sms.py.

Kept as its own thin module (rather than calling these three things
directly from the route/conflict-resolver code) so the call site only
needs to know "notify about this status change" — not which channels
exist or how each one is contacted.

A channel failure (bad SMTP config, Twilio error, etc.) is caught and
logged here rather than allowed to propagate — sending a notification is
a side effect of a status update, and must never be the reason the
actual status update itself fails.
"""

import os
from sqlalchemy.orm import Session

from app.services.email import send_status_notification_email
from app.services.sms import send_status_notification_sms
from app.models.customer_notification import CustomerNotificationDB

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

STATUS_LABELS = {
    "confirmed": "Order Confirmed",
    "picked_up": "Picked Up",
    "out_for_delivery": "Out for Delivery",
    "delivered": "Delivered",
    "failed_attempt": "Delivery Attempt Failed",
}


def notify_customer_of_status_change(
    db: Session,
    delivery_id: str,
    order_id: str,
    new_status: str,
    customer_email: str | None,
    customer_phone: str | None,
    customer_id: str | None = None,
) -> None:
    status_label = STATUS_LABELS.get(new_status, new_status)
    tracking_link = f"{FRONTEND_URL}/?track={delivery_id}"

    # 1. In-app notification — the real, primary channel
    if customer_id:
        try:
            notification = CustomerNotificationDB(
                customer_id=customer_id,
                delivery_id=delivery_id,
                order_id=order_id,
                message=f"Order {order_id} is now: {status_label}",
            )
            db.add(notification)
            db.commit()
        except Exception as error:  # noqa: BLE001
            print(f"In-app notification failed for {order_id}: {error}")

    # 2. Email — secondary, best-effort
    if customer_email:
        try:
            send_status_notification_email(customer_email, order_id, status_label, tracking_link)
        except Exception as error:  # noqa: BLE001
            print(f"Email notification failed for {order_id}: {error}")

    # 3. SMS — secondary, best-effort
    if customer_phone:
        try:
            send_status_notification_sms(customer_phone, order_id, status_label, tracking_link)
        except Exception as error:  # noqa: BLE001
            print(f"SMS notification failed for {order_id}: {error}")
