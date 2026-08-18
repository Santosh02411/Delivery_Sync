"""
FastAPI application entry point.

Run with:  uvicorn main:app --reload
Then visit http://127.0.0.1:8000/docs for the interactive Swagger UI —
a genuinely useful thing to show in interviews, since it's auto-generated
from your code.
"""

# THIS MUST RUN BEFORE ANY OTHER LOCAL IMPORT. Several modules below
# (services/email.py, services/sms.py, services/payment.py, services/push.py)
# read config like SMTP_HOST/TWILIO_ACCOUNT_SID/RAZORPAY_KEY_ID as
# MODULE-LEVEL constants — evaluated once, the moment that module is
# first imported. If .env hasn't been loaded into the process
# environment yet at that exact moment, those modules permanently
# capture `None` for every setting, and no amount of the .env file
# being technically present on disk fixes it for the rest of that
# run — env vars only reach os.environ this way, never automatically
# just because a same-named file sits next to the code. Filling in a
# real SMTP_HOST in .env and still seeing "printed instead of sent" in
# the console is exactly what that bug looks like from the outside.
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import logging
import os

from app.db.session import Base, engine
from app.db.migrate import run_lightweight_migrations
from app.services.rate_limiter import limiter
from app.routes import deliveries, sync, auth, users, bulk_import, admin, export, messages, tracking, customer_auth, customer_dashboard, customer_privacy, stores, products, cart, checkout, reviews, coupons, analytics, slots

# Create all database tables on startup (if they don't already exist),
# then catch up any EXISTING table to the model's current columns — see
# app/db/migrate.py's module docstring for why both steps are needed:
# create_all() alone only handles brand-new tables, never adds columns
# to ones that already exist from a previous run.
Base.metadata.create_all(bind=engine)
run_lightweight_migrations(engine, Base)

app = FastAPI(
    title="Offline-First Delivery Sync API",
    description="Backend for the offline-first delivery status tracking project",
    version="0.1.0",
)

# Rate limiting: protects against brute-force login attempts, signup spam,
# and abuse of the unauthenticated /sync endpoint. Uses in-memory storage
# (no Redis needed) — fine for a single-server deployment; a multi-server
# production deployment would need a shared store (e.g. Redis-backed
# limiter) so limits are enforced consistently across all instances.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Allow the React frontend (running on a different port) to call this API.
# NOTE: allow_origins=["*"] combined with allow_credentials=True is rejected
# by browsers (invalid combination per the fetch spec) and causes silent
# "Failed to fetch" errors on the client. Since this app doesn't use
# cookies/sessions, we set allow_credentials=False and can safely allow all
# origins for local development. For a real deployment, set the
# ALLOWED_ORIGINS environment variable to a comma-separated list of exact
# frontend URLs instead of leaving this wide open.
allowed_origins_env = os.environ.get("ALLOWED_ORIGINS")
allowed_origins = allowed_origins_env.split(",") if allowed_origins_env else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serves uploaded product images back out at /uploads/products/<file>.
# Created on demand by routes/products.py's upload endpoint, so it may
# not exist yet on a completely fresh checkout — create it here too so
# the mount never fails on a clean clone.
UPLOAD_ROOT = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(os.path.join(UPLOAD_ROOT, "products"), exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_ROOT), name="uploads")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """
    A few standard, low-risk security headers. These don't replace proper
    hardening (see docs/SECURITY_AND_ACCESS.md for the full list of what's
    still needed before a real public deployment — HTTPS enforcement,
    a production-grade secret key, a shared-store rate limiter, etc.) but
    they're free, safe defaults worth having regardless.

    IMPORTANT — why this wraps call_next in try/except instead of just
    awaiting it directly: `@app.middleware("http")` is Starlette's
    BaseHTTPMiddleware, which runs the rest of the app in a separate
    anyio task inside a task group. If an unhandled exception happens
    deep in a route (see routes/checkout.py's Razorpay call for a real
    example that hit this), that combination can fail to deliver a
    clean 500 JSON response back to the client at all — the browser
    just sees the connection drop, `fetch()` throws a bare TypeError,
    and the frontend (reasonably, but wrongly) reports "you're offline"
    even though the real problem was a backend crash. The individual
    route-level fix is to never let external calls (payment gateways,
    etc.) raise uncaught — but this except clause is the backstop for
    any OTHER unhandled exception anywhere in the app: it guarantees the
    client always gets back a real 500 JSON response instead of a
    dropped connection, so a genuine crash always shows up as a genuine
    error message, never a misleading "offline" one.
    """
    try:
        response = await call_next(request)
    except Exception:
        logging.getLogger(__name__).exception("Unhandled exception for %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

app.include_router(export.router)
app.include_router(deliveries.router)
app.include_router(sync.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(bulk_import.router)
app.include_router(admin.router)
app.include_router(messages.router)
app.include_router(tracking.router)
app.include_router(customer_auth.router)
app.include_router(customer_dashboard.router)
app.include_router(customer_privacy.router)
app.include_router(stores.router)
app.include_router(products.router)
app.include_router(products.store_router)
app.include_router(cart.router)
app.include_router(checkout.router)
app.include_router(reviews.router)
app.include_router(coupons.router)
app.include_router(analytics.router)
app.include_router(slots.router)


@app.get("/")
def root():
    return {"message": "Delivery Sync API is running. Visit /docs for API documentation."}
