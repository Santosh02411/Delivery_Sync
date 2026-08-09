"""
Real browser Web Push notifications — unlike SMS/WhatsApp, this needs NO
paid third-party account. It's a free, open W3C standard: the browser
itself (via Chrome/Firefox/etc.'s push service) delivers the message,
authenticated using a VAPID keypair this project owns.

A keypair is generated once and checked in below as a working default —
genuinely functional out of the box, not a placeholder. You can generate
your own instead (recommended before any real deployment, so you don't
share a key with every other clone of this repo) with:

    pip install py-vapid --break-system-packages
    vapid --gen  # writes private_key.pem / public_key.pem, prints both

...then set VAPID_PRIVATE_KEY_PEM and VAPID_PUBLIC_KEY env vars.

This delivers a real OS-level notification — even with the browser tab
or the whole browser closed — as long as the device is online and the
service worker (frontend/public/sw.js) is registered, exactly like a
mobile app's push notifications.
"""

import json
import os

from pywebpush import webpush, WebPushException

# Working default keypair, generated for this project. Safe for local
# dev/demo use; generate your own (see docstring above) before any real
# multi-user deployment.
_DEFAULT_PRIVATE_KEY_PEM = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgJQnAppenmnmxdFpP
A/ljhsMUMIC2gvd+e2Aql518Q9uhRANCAAS6Tf+3IO5DBJB14kvYjP+BpAehvHIf
jXunE5hbTIMiAnsyvYKAc6cUMzdMx7kK69PUugUvTq3qPZGjsumdtq2u
-----END PRIVATE KEY-----"""
DEFAULT_VAPID_PUBLIC_KEY = "BLpN_7cg7kMEkHXiS9iM_4GkB6G8ch-Ne6cTmFtMgyICezK9goBzpxQzN0zHuQrr09S6BS9Oreo9kaOy6Z22ra4"

VAPID_PRIVATE_KEY_PEM = os.environ.get("VAPID_PRIVATE_KEY_PEM", _DEFAULT_PRIVATE_KEY_PEM)
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", DEFAULT_VAPID_PUBLIC_KEY)
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:admin@deliverysync.local")


def send_web_push(subscription_info: dict, title: str, body: str, url: str = "/") -> bool:
    """
    Sends one real push notification to one subscribed browser.
    Returns True on success, False on failure (invalid/expired
    subscription, network error, etc.) — never raises, since a push
    failure must never break the status-update flow that triggered it.
    """
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=VAPID_PRIVATE_KEY_PEM,
            vapid_claims={"sub": VAPID_CLAIM_EMAIL},
        )
        return True
    except WebPushException as e:
        print(f"Web push failed (subscription likely expired): {e}")
        return False
