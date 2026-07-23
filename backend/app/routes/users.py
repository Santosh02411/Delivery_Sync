"""
Routes for user-related lookups. Currently just one: letting a dispatcher
fetch the list of registered agents, so they can pick who to assign a new
delivery to.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.models.user import UserDB, UserRole, UserOut
from app.routes.deliveries import require_dispatcher

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/agents", response_model=List[UserOut])
def list_agents(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_dispatcher),
):
    """Dispatcher-only: list all registered agents, for the assignment dropdown."""
    return db.query(UserDB).filter(UserDB.role == UserRole.agent).all()
