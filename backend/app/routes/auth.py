"""
Auth routes: signup and login. Also exposes `get_current_user`, a FastAPI
dependency that other routes use to identify who's making a request and
check their role — this is what makes role-based access possible (e.g. an
agent can only see their own deliveries, a dispatcher sees everyone's).
"""

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.user import UserDB, UserSignup, UserLogin, UserOut, TokenResponse
from app.services.auth import hash_password, verify_password, create_access_token, decode_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse)
def signup(payload: UserSignup, db: Session = Depends(get_db)):
    existing = db.query(UserDB).filter(UserDB.username == payload.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="That username's already taken. Try another.")

    user = UserDB(
        username=payload.username,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        display_name=payload.display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": user.id, "role": user.role.value})
    return {"access_token": token, "user": user}


@router.post("/login", response_model=TokenResponse)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.username == payload.username).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect username or password.")

    token = create_access_token({"sub": user.id, "role": user.role.value})
    return {"access_token": token, "user": user}


def get_current_user(
    authorization: str = Header(None), db: Session = Depends(get_db)
) -> UserDB:
    """
    FastAPI dependency: extracts and validates the JWT from the
    'Authorization: Bearer <token>' header, and returns the corresponding
    user. Any route that depends on this requires a logged-in user.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Your session expired. Log in again.")

    token = authorization.split(" ")[1]
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Your session expired. Log in again.")

    user = db.query(UserDB).filter(UserDB.id == payload.get("sub")).first()
    if not user:
        raise HTTPException(status_code=401, detail="Your session expired. Log in again.")

    return user
