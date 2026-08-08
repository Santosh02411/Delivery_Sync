"""
FastAPI application entry point.

Run with:  uvicorn main:app --reload
Then visit http://127.0.0.1:8000/docs for the interactive Swagger UI —
a genuinely useful thing to show in interviews, since it's auto-generated
from your code.
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import os

from app.db.session import Base, engine
from app.services.rate_limiter import limiter
from app.routes import deliveries, sync, auth, users, bulk_import, admin, export, messages, tracking, customer_auth, customer_dashboard

# Create all database tables on startup (if they don't already exist)
Base.metadata.create_all(bind=engine)

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


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """
    A few standard, low-risk security headers. These don't replace proper
    hardening (see docs/SECURITY_AND_ACCESS.md for the full list of what's
    still needed before a real public deployment — HTTPS enforcement,
    a production-grade secret key, a shared-store rate limiter, etc.) but
    they're free, safe defaults worth having regardless.
    """
    response = await call_next(request)
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


@app.get("/")
def root():
    return {"message": "Delivery Sync API is running. Visit /docs for API documentation."}
