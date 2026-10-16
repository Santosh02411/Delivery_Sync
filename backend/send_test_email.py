"""
Sends one test email using the settings in backend/.env and prints exactly
what happened — the quickest way to find out why real emails aren't arriving.

    cd backend
    python send_test_email.py you@example.com          # send a test
    python send_test_email.py you@example.com --debug  # plus the full SMTP conversation
"""

import sys

from dotenv import load_dotenv

load_dotenv()

if "--debug" in sys.argv:
    import os
    os.environ["SMTP_DEBUG"] = "1"

from app.services import email as email_svc  # noqa: E402  (must import AFTER load_dotenv)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Usage: python send_test_email.py you@example.com [--debug]")
        return 2
    to = args[0]

    print(email_svc.describe_email_config())
    if not email_svc.SMTP_HOST:
        print("\nNothing will be sent until SMTP_HOST is set in backend/.env (and the backend/script is restarted).")
        return 1

    try:
        email_svc.send_customer_welcome_email(to, "Test User")
    except Exception:  # details + hint were already printed by the email module
        return 1
    print(f"\nSent. Check the inbox (and Spam) of {to}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
