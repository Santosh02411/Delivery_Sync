"""
Admin routes: manage users within the admin's own organization —
view all users, deactivate/reactivate accounts, and reset a user's
password directly.

Honest limitation, worth stating plainly: there's no email service
available (no budget for one), so "reset password" here means the admin
sets a new password directly and communicates it to the user
out-of-band (in person, chat, etc.) — NOT an emailed reset link, which is
what a production system would use instead. This is documented as a
known gap in docs/SECURITY_AND_ACCESS.md.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List

from app.db.session import get_db
from app.models.user import UserDB, UserRole, UserOut
from app.models.organization import OrganizationDB, OrganizationOut
from app.routes.auth import get_current_user
from app.services.auth import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(current_user: UserDB = Depends(get_current_user)) -> UserDB:
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Only admins can do this.")
    return current_user


class ResetPasswordRequest(BaseModel):
    new_password: str


@router.get("/organization", response_model=OrganizationOut)
def get_my_organization(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin),
):
    """
    Returns the admin's own organization, including its invite_code — this
    is what makes good on the promise shown at signup ("any admin can look
    it up later"), since the code is otherwise only ever shown once, at
    the moment the organization is first created.
    """
    org = db.query(OrganizationDB).filter(OrganizationDB.id == current_user.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")
    return org


@router.get("/users", response_model=List[UserOut])
def list_organization_users(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin),
):
    """List every user (agent, dispatcher, admin) in the admin's own organization."""
    return db.query(UserDB).filter(UserDB.org_id == current_user.org_id).all()


@router.patch("/users/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You can't deactivate your own account.")

    target = db.query(UserDB).filter(
        UserDB.id == user_id, UserDB.org_id == current_user.org_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found in your organization.")

    target.is_active = False
    db.commit()
    db.refresh(target)
    return target


@router.patch("/users/{user_id}/activate", response_model=UserOut)
def activate_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin),
):
    target = db.query(UserDB).filter(
        UserDB.id == user_id, UserDB.org_id == current_user.org_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found in your organization.")

    target.is_active = True
    db.commit()
    db.refresh(target)
    return target


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: str,
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin),
):
    target = db.query(UserDB).filter(
        UserDB.id == user_id, UserDB.org_id == current_user.org_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found in your organization.")

    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters.")

    target.hashed_password = hash_password(payload.new_password)
    db.commit()
    return {"success": True, "message": f"Password reset for {target.display_name}."}
