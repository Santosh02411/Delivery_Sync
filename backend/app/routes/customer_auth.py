"""
Customer authentication — separate signup/login from the staff
(agent/dispatcher/admin) system in routes/auth.py. Reuses the same
password hashing and JWT helpers from services/auth.py, but issues
tokens with a distinct payload shape ({"customer_id": ...} instead of
{"sub": ..., "role": ...}) so a customer token and a staff token can
never be confused with each other, even accidentally.
"""

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.customer import CustomerDB, CustomerSignup, CustomerLogin, CustomerTokenResponse, CustomerOut, CustomerProfileUpdate, CustomerPasswordChange
from app.models.delivery import DeliveryRecordDB
from app.services.auth import hash_password, verify_password, create_access_token, decode_access_token
from app.services.rate_limiter import limiter

router = APIRouter(prefix="/customer", tags=["customer-auth"])


@router.post("/signup", response_model=CustomerTokenResponse)
@limiter.limit("5/minute")
def customer_signup(request: Request, payload: CustomerSignup, db: Session = Depends(get_db)):
    existing = db.query(CustomerDB).filter(CustomerDB.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with that email already exists. Try logging in.")

    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

    customer = CustomerDB(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        name=payload.name,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)

    # Retroactively link any existing deliveries that used this email as
    # customer_email but were created before this account existed — so
    # signing up doesn't leave past orders stranded outside the dashboard.
    db.query(DeliveryRecordDB).filter(
        DeliveryRecordDB.customer_email == payload.email,
        DeliveryRecordDB.customer_id.is_(None),
    ).update({"customer_id": customer.id})
    db.commit()

    token = create_access_token({"customer_id": customer.id})
    return {"access_token": token, "customer": customer}


@router.post("/login", response_model=CustomerTokenResponse)
@limiter.limit("10/minute")
def customer_login(request: Request, payload: CustomerLogin, db: Session = Depends(get_db)):
    customer = db.query(CustomerDB).filter(CustomerDB.email == payload.email).first()
    if not customer or not verify_password(payload.password, customer.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    token = create_access_token({"customer_id": customer.id})
    return {"access_token": token, "customer": customer}


def get_current_customer(
    authorization: str = Header(None), db: Session = Depends(get_db)
) -> CustomerDB:
    """FastAPI dependency mirroring get_current_user, but for customer tokens."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Your session expired. Log in again.")

    token = authorization.split(" ")[1]
    payload = decode_access_token(token)
    if not payload or "customer_id" not in payload:
        raise HTTPException(status_code=401, detail="Your session expired. Log in again.")

    customer = db.query(CustomerDB).filter(CustomerDB.id == payload["customer_id"]).first()
    if not customer:
        raise HTTPException(status_code=401, detail="Your session expired. Log in again.")

    return customer


@router.get("/me", response_model=CustomerOut)
def get_my_profile(current_customer: CustomerDB = Depends(get_current_customer)):
    return current_customer


@router.patch("/me", response_model=CustomerOut)
def update_my_profile(
    payload: CustomerProfileUpdate,
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name can't be empty.")
        current_customer.name = name

    if payload.email is not None:
        email = payload.email.strip()
        if not email:
            raise HTTPException(status_code=400, detail="Email can't be empty.")
        if email != current_customer.email:
            existing = db.query(CustomerDB).filter(CustomerDB.email == email).first()
            if existing:
                raise HTTPException(status_code=400, detail="Another account already uses that email.")
            current_customer.email = email

    db.commit()
    db.refresh(current_customer)
    return current_customer


@router.post("/me/change-password")
def change_my_password(
    payload: CustomerPasswordChange,
    db: Session = Depends(get_db),
    current_customer: CustomerDB = Depends(get_current_customer),
):
    if not verify_password(payload.current_password, current_customer.hashed_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters.")

    current_customer.hashed_password = hash_password(payload.new_password)
    db.commit()
    return {"message": "Password changed."}
