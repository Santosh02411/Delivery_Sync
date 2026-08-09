"""
ProductDB: an item a store (organization) sells. Org-scoped — each
organization manages its own catalog, and customers browse one store's
catalog at a time (see routes/stores.py for the public browsing side).
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Float, Boolean
from pydantic import BaseModel
from typing import Optional

from app.db.session import Base


class ProductDB(Base):
    __tablename__ = "products"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id = Column(String, index=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    price = Column(Float, nullable=False)  # in rupees (or your currency's major unit)
    image_url = Column(String, nullable=True)
    category = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)  # inactive products stay in history but stop showing in the storefront
    created_at = Column(DateTime, nullable=False)


class ProductCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    image_url: Optional[str] = None
    category: Optional[str] = None
    is_active: bool = True


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    image_url: Optional[str] = None
    category: Optional[str] = None
    is_active: Optional[bool] = None


class ProductOut(BaseModel):
    id: str
    org_id: str
    name: str
    description: Optional[str] = None
    price: float
    image_url: Optional[str] = None
    category: Optional[str] = None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True
