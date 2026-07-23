"""
FastAPI application entry point.

Run with:  uvicorn main:app --reload
Then visit http://127.0.0.1:8000/docs for the interactive Swagger UI —
a genuinely useful thing to show in interviews, since it's auto-generated
from your code.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.session import Base, engine
from app.routes import deliveries, sync, auth, users

# Create all database tables on startup (if they don't already exist)
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Offline-First Delivery Sync API",
    description="Backend for the offline-first delivery status tracking project",
    version="0.1.0",
)

# Allow the React frontend (running on a different port) to call this API.
# NOTE: allow_origins=["*"] combined with allow_credentials=True is rejected
# by browsers (invalid combination per the fetch spec) and causes silent
# "Failed to fetch" errors on the client. Since this app doesn't use
# cookies/sessions, we set allow_credentials=False and can safely allow all
# origins for local development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(deliveries.router)
app.include_router(sync.router)
app.include_router(auth.router)
app.include_router(users.router)


@app.get("/")
def root():
    return {"message": "Delivery Sync API is running. Visit /docs for API documentation."}
