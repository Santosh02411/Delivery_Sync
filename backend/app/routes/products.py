"""
Product catalog management — dispatcher/admin manage their own org's
catalog here. The public-facing browsing side (what customers see)
lives separately in routes/stores.py, which only ever exposes active
products from orgs that opted into is_public_store.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.models.product import ProductDB, ProductCreate, ProductUpdate, ProductOut
from app.models.user import UserDB
from app.models.organization import OrganizationDB, StoreVisibilityUpdate, OrganizationOut
from app.routes.deliveries import require_dispatcher
from app.routes.admin import require_admin

router = APIRouter(prefix="/admin/products", tags=["products"])


@router.get("/", response_model=List[ProductOut])
def list_my_products(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_dispatcher),
):
    """Dispatcher/admin view of their own org's full catalog, including inactive products."""
    return db.query(ProductDB).filter(ProductDB.org_id == current_user.org_id).all()


@router.post("/", response_model=ProductOut)
def create_product(
    payload: ProductCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_dispatcher),
):
    product = ProductDB(
        id=str(uuid.uuid4()),
        org_id=current_user.org_id,
        name=payload.name,
        description=payload.description,
        price=payload.price,
        image_url=payload.image_url,
        category=payload.category,
        is_active=payload.is_active,
        created_at=datetime.utcnow(),
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: str,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_dispatcher),
):
    product = db.query(ProductDB).filter(
        ProductDB.id == product_id,
        ProductDB.org_id == current_user.org_id,
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)

    db.commit()
    db.refresh(product)
    return product


@router.delete("/{product_id}")
def delete_product(
    product_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_dispatcher),
):
    product = db.query(ProductDB).filter(
        ProductDB.id == product_id,
        ProductDB.org_id == current_user.org_id,
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    db.delete(product)
    db.commit()
    return {"deleted": True}


store_router = APIRouter(prefix="/admin/store", tags=["products"])


@store_router.patch("/visibility", response_model=OrganizationOut)
def set_store_visibility(
    payload: StoreVisibilityUpdate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin),
):
    """Admin-only: turn the org's public storefront on/off."""
    org = db.query(OrganizationDB).filter(OrganizationDB.id == current_user.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")
    org.is_public_store = payload.is_public_store
    db.commit()
    db.refresh(org)
    return org
