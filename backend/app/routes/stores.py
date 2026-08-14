"""
Public storefront browsing — no login required, same as browsing any
e-commerce site before signing in. Only organizations that opted in via
is_public_store show up here (see organization.py) and only their
active products are listed.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.models.organization import OrganizationDB, PublicOrganizationOut
from app.models.product import ProductDB, ProductOut
from app.routes.products import _attach_review_stats

router = APIRouter(prefix="/stores", tags=["storefront"])


@router.get("/", response_model=List[PublicOrganizationOut])
def list_public_stores(db: Session = Depends(get_db)):
    return db.query(OrganizationDB).filter(OrganizationDB.is_public_store == True).all()  # noqa: E712


@router.get("/{org_id}/products", response_model=List[ProductOut])
def list_store_products(org_id: str, db: Session = Depends(get_db)):
    store = db.query(OrganizationDB).filter(
        OrganizationDB.id == org_id,
        OrganizationDB.is_public_store == True,  # noqa: E712
    ).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found.")

    products = db.query(ProductDB).filter(
        ProductDB.org_id == org_id,
        ProductDB.is_active == True,  # noqa: E712
    ).all()
    return _attach_review_stats(db, products)
