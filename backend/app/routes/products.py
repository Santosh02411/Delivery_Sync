"""
Product catalog management — dispatcher/admin manage their own org's
catalog here. The public-facing browsing side (what customers see)
lives separately in routes/stores.py, which only ever exposes active
products from orgs that opted into is_public_store.
"""

import asyncio
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.models.product import (
    ProductDB, ProductCreate, ProductUpdate, ProductOut, ProductImageUploadOut,
    BulkProductImportRequest, BulkProductImportResponse, BulkProductRowResult,
)
from app.models.product_review import ProductReviewDB
from app.models.user import UserDB
from app.models.organization import OrganizationDB, StoreVisibilityUpdate, StorePricingUpdate, StoreSlotSettingsUpdate, StoreProfileUpdate, OrganizationOut
from app.routes.deliveries import require_dispatcher
from app.routes.admin import require_admin
from app.services.action_log import record_action
from app.services.image_upload import ImageRejected, MAX_IMAGE_BYTES, read_upload_bounded, sanitize_image
from app.services.object_storage import get_image_store
from app.services.rate_limiter import limiter

router = APIRouter(prefix="/admin/products", tags=["products"])

logger = logging.getLogger(__name__)

# Uploaded images are validated and sanitized by services/image_upload.py and stored by
# services/object_storage.py (local disk or S3-compatible storage, chosen by UPLOAD_STORAGE).
# The request body is size-limited BEFORE it is parsed by services/upload_limits.py (installed in
# main.py), because the framework reads the whole body before this route runs. See docs/UPLOADS_AND_STORAGE.md.

# Decoding an image is CPU/memory heavy: at most this many at once, so a burst of uploads can't exhaust
# the server's memory (the rest wait their turn).
_decode_slots = asyncio.Semaphore(2)


def _attach_review_stats(db: Session, products: List[ProductDB]) -> List[ProductOut]:
    """
    Product ratings live in a separate table (ProductReviewDB), not as
    columns on ProductDB — an average recomputed from real rows is
    always correct, vs. a cached counter that can drift. Computed here,
    once per list call, rather than per-product N+1 queries.
    """
    if not products:
        return []
    product_ids = [p.id for p in products]
    rows = (
        db.query(ProductReviewDB.product_id, ProductReviewDB.rating)
        .filter(ProductReviewDB.product_id.in_(product_ids))
        .all()
    )
    sums: dict = {}
    counts: dict = {}
    for product_id, rating in rows:
        sums[product_id] = sums.get(product_id, 0) + rating
        counts[product_id] = counts.get(product_id, 0) + 1

    results = []
    for p in products:
        out = ProductOut.model_validate(p)
        count = counts.get(p.id, 0)
        out.review_count = count
        out.average_rating = round(sums[p.id] / count, 1) if count else None
        results.append(out)
    return results


@router.get("/", response_model=List[ProductOut])
def list_my_products(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_dispatcher),
):
    """Dispatcher/admin view of their own org's full catalog, including inactive products."""
    products = db.query(ProductDB).filter(ProductDB.org_id == current_user.org_id).all()
    return _attach_review_stats(db, products)


@router.post("/upload-image", response_model=ProductImageUploadOut)
@limiter.limit("60/minute")
async def upload_product_image(
    request: Request,
    file: UploadFile = File(...),
    current_user: UserDB = Depends(require_dispatcher),
):
    """
    Real file upload: takes the actual image bytes (multipart/form-data), proves they ARE a JPEG, PNG,
    WebP or GIF, stores a cleaned copy under a random name, and returns the URL to plug straight into a
    product's image_url field.

    What is NOT trusted: the declared Content-Type, the file name, and the file's size claims. The body is
    read in small chunks and refused the moment it passes the limit (413); the format is decided from the
    file's bytes; dimensions are checked from the header before any pixel is decoded (so a tiny file that
    expands to gigapixels is refused); the image is fully decoded (corrupt/truncated files are refused),
    and JPEG/PNG/WebP are re-encoded, which drops EXIF/GPS metadata and any appended script. Details in
    services/image_upload.py.
    """
    try:
        data = await read_upload_bounded(file.read, MAX_IMAGE_BYTES)
    except ImageRejected as error:
        raise HTTPException(status_code=error.status, detail=error.message)
    finally:
        await file.close()

    declared = (file.content_type or "").lower()
    async with _decode_slots:
        try:
            image = await asyncio.to_thread(sanitize_image, data)  # CPU-bound: keep it off the event loop
        except ImageRejected as error:
            raise HTTPException(status_code=error.status, detail=error.message)
    if declared and declared != image.content_type:
        # Not an error (a mislabelled but genuine image is harmless; the stored type is the detected one) — just worth knowing.
        logger.info("Upload declared as %s was detected as %s.", declared, image.content_type)

    try:
        url = await asyncio.to_thread(get_image_store().save, image.data, image.extension, image.content_type)
    except Exception:  # noqa: BLE001
        logger.exception("Could not store an uploaded product image.")
        raise HTTPException(status_code=503, detail="Couldn't save the image right now. Please try again.")

    return ProductImageUploadOut(image_url=url)


# ---------- Bulk import from CSV ----------

_TRUE_WORDS = {"true", "yes", "y", "1", "active"}
_FALSE_WORDS = {"false", "no", "n", "0", "inactive"}
MAX_NAME_LENGTH = 200
MAX_TEXT_LENGTH = 2000


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_number(value, label: str, *, integer: bool = False) -> float | int:
    """Accepts '12', '12.50', '₹1,299.00', 'Rs. 499' — what people actually type in spreadsheets."""
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number.")
    if isinstance(value, (int, float)):
        number = value
    else:
        cleaned = str(value).strip().replace(",", "").replace("₹", "").replace("$", "")
        cleaned = cleaned.lower().replace("rs.", "").replace("rs", "").strip()
        try:
            number = float(cleaned)
        except ValueError:
            raise ValueError(f"{label} \"{value}\" isn't a number.")
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{label} must be a finite number.")
    if number < 0:
        raise ValueError(f"{label} can't be negative.")
    if integer:
        if int(number) != number:
            raise ValueError(f"{label} must be a whole number.")
        return int(number)
    return float(number)


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    word = str(value).strip().lower()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    raise ValueError(f"is_active \"{value}\" should be yes/no, true/false or 1/0.")


def _clean_text(value, label: str, max_length: int) -> str | None:
    if _blank(value):
        return None
    text = str(value).strip()
    if len(text) > max_length:
        raise ValueError(f"{label} is too long (max {max_length} characters).")
    return text


def _clean_image_url(value) -> str | None:
    url = _clean_text(value, "image_url", 1000)
    if url is None:
        return None
    # Only real web links or images already uploaded to this server — never javascript:/data: URLs.
    if not (url.lower().startswith(("http://", "https://")) or url.startswith("/uploads/")):
        raise ValueError("image_url must start with http://, https:// or /uploads/.")
    return url


def _parse_product_row(row) -> dict:
    """Validates one CSV row; returns clean fields (only the ones the row actually provided)
    or raises ValueError with a message meant for the person who wrote the CSV."""
    fields: dict = {}
    name = _clean_text(row.name, "name", MAX_NAME_LENGTH)
    if name is None:
        raise ValueError("name is required.")
    fields["name"] = name
    if _blank(row.price):
        raise ValueError("price is required.")
    fields["price"] = _parse_number(row.price, "price")
    if not _blank(row.description):
        fields["description"] = _clean_text(row.description, "description", MAX_TEXT_LENGTH)
    if not _blank(row.category):
        fields["category"] = _clean_text(row.category, "category", 100)
    if not _blank(row.image_url):
        fields["image_url"] = _clean_image_url(row.image_url)
    if not _blank(row.is_active):
        fields["is_active"] = _parse_bool(row.is_active)
    if not _blank(row.stock_quantity):
        fields["stock_quantity"] = _parse_number(row.stock_quantity, "stock_quantity", integer=True)
    if not _blank(row.cost_price):
        fields["cost_price"] = _parse_number(row.cost_price, "cost_price")
    return fields


@router.post("/bulk-import", response_model=BulkProductImportResponse)
def bulk_import_products(
    payload: BulkProductImportRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_dispatcher),
):
    """
    Create (or, with update_existing, refresh) many products from a CSV at once.

    Same philosophy as the delivery bulk import: every row is validated and saved on its
    own, so one typo in row 47 of a 300-row file is reported against row 47 while the other
    299 still go through. Products are matched by NAME within your organization
    (case-insensitive); a duplicate name — whether it already exists or appears twice in the
    same file — is skipped unless `update_existing` is on, in which case the existing product
    is updated with whatever columns the row filled in (blank cells leave the old value alone).
    """
    existing = {
        p.name.strip().lower(): p
        for p in db.query(ProductDB).filter(ProductDB.org_id == current_user.org_id).all()
    }
    results: List[BulkProductRowResult] = []
    created = updated = failed = 0
    now = datetime.utcnow()

    for index, row in enumerate(payload.rows, start=1):
        label = str(row.name).strip() if not _blank(row.name) else ""
        try:
            fields = _parse_product_row(row)
            key = fields["name"].lower()
            match = existing.get(key)
            if match and not payload.update_existing:
                raise ValueError(f"A product named \"{fields['name']}\" already exists (turn on \"update existing\" to change it).")
            if match:
                for column, value in fields.items():
                    setattr(match, column, value)
                db.commit()
                results.append(BulkProductRowResult(row_number=index, name=fields["name"], success=True, action="updated", product_id=match.id))
                updated += 1
            else:
                product = ProductDB(
                    id=str(uuid.uuid4()), org_id=current_user.org_id, created_at=now,
                    is_active=fields.pop("is_active", True), **fields,
                )
                db.add(product)
                db.commit()
                existing[key] = product  # a second row with the same name in this file is now a duplicate
                results.append(BulkProductRowResult(row_number=index, name=product.name, success=True, action="created", product_id=product.id))
                created += 1
        except ValueError as error:
            db.rollback()
            results.append(BulkProductRowResult(row_number=index, name=label, success=False, error=str(error)))
            failed += 1

    if created or updated:
        record_action(
            db, org_id=current_user.org_id,
            actor_user_id=current_user.id, actor_display_name=current_user.display_name,
            action="product.bulk_import", entity_type="product", entity_id=None,
            entity_label=f"{created + updated} products",
            summary=f"Imported products from CSV: {created} created, {updated} updated, {failed} failed",
        )
    return BulkProductImportResponse(results=results, created_count=created, updated_count=updated, failure_count=failed)


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
        stock_quantity=payload.stock_quantity,
        cost_price=payload.cost_price,
        created_at=datetime.utcnow(),
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    record_action(
        db, org_id=current_user.org_id,
        actor_user_id=current_user.id, actor_display_name=current_user.display_name,
        action="product.create", entity_type="product", entity_id=product.id,
        entity_label=product.name,
        summary=f"Created product {product.name}",
    )
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

    updates = payload.model_dump(exclude_unset=True)
    before = {field: getattr(product, field) for field in updates}

    for field, value in updates.items():
        setattr(product, field, value)

    db.commit()
    db.refresh(product)
    record_action(
        db, org_id=current_user.org_id,
        actor_user_id=current_user.id, actor_display_name=current_user.display_name,
        action="product.update", entity_type="product", entity_id=product.id,
        entity_label=product.name,
        summary=f"Updated product {product.name}",
        before=before, after=updates,
    )
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

    # Best-effort cleanup of the stored image (disk or object storage). It only ever acts on URLs this
    # app's own store produced — an external link, or a crafted path, is ignored — and a failure here
    # must never block deleting the product itself.
    get_image_store().delete(product.image_url)

    product_name = product.name
    db.delete(product)
    db.commit()
    record_action(
        db, org_id=current_user.org_id,
        actor_user_id=current_user.id, actor_display_name=current_user.display_name,
        action="product.delete", entity_type="product", entity_id=product_id,
        entity_label=product_name,
        summary=f"Deleted product {product_name}",
    )
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
    record_action(
        db, org_id=current_user.org_id,
        actor_user_id=current_user.id, actor_display_name=current_user.display_name,
        action="store_settings.update", entity_type="store_settings", entity_id=org.id,
        entity_label="Storefront visibility",
        summary=f"Set storefront visibility to {'public' if payload.is_public_store else 'private'}",
    )
    return org


@store_router.patch("/pricing", response_model=OrganizationOut)
def set_store_pricing(
    payload: StorePricingUpdate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin),
):
    """Admin-only: set the org's flat delivery fee and GST/tax rate, applied to every checkout at this store."""
    if payload.delivery_fee < 0 or payload.tax_rate_percent < 0:
        raise HTTPException(status_code=400, detail="Delivery fee and tax rate can't be negative.")
    org = db.query(OrganizationDB).filter(OrganizationDB.id == current_user.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")
    org.delivery_fee = payload.delivery_fee
    org.tax_rate_percent = payload.tax_rate_percent
    db.commit()
    db.refresh(org)
    record_action(
        db, org_id=current_user.org_id,
        actor_user_id=current_user.id, actor_display_name=current_user.display_name,
        action="store_settings.update", entity_type="store_settings", entity_id=org.id,
        entity_label="Pricing",
        summary=f"Set delivery fee to {payload.delivery_fee} and tax rate to {payload.tax_rate_percent}%",
    )
    return org


@store_router.patch("/slot-settings", response_model=OrganizationOut)
def set_store_slot_settings(
    payload: StoreSlotSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin),
):
    """Admin-only: configure the store's bookable delivery-window grid — daily operating hours, slot length, and how many orders each slot can hold."""
    if payload.slot_duration_minutes <= 0:
        raise HTTPException(status_code=400, detail="Slot duration must be a positive number of minutes.")
    if not (0 <= payload.slot_window_start_hour <= 23) or not (0 <= payload.slot_window_end_hour <= 23):
        raise HTTPException(status_code=400, detail="Window hours must be between 0 and 23.")
    if payload.slot_window_end_hour <= payload.slot_window_start_hour:
        raise HTTPException(status_code=400, detail="The window's end hour must be after its start hour.")
    if payload.max_orders_per_slot <= 0:
        raise HTTPException(status_code=400, detail="Max orders per slot must be at least 1.")

    org = db.query(OrganizationDB).filter(OrganizationDB.id == current_user.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")
    org.slot_duration_minutes = payload.slot_duration_minutes
    org.slot_window_start_hour = payload.slot_window_start_hour
    org.slot_window_end_hour = payload.slot_window_end_hour
    org.max_orders_per_slot = payload.max_orders_per_slot
    db.commit()
    db.refresh(org)
    record_action(
        db, org_id=current_user.org_id,
        actor_user_id=current_user.id, actor_display_name=current_user.display_name,
        action="store_settings.update", entity_type="store_settings", entity_id=org.id,
        entity_label="Delivery slot settings",
        summary="Updated delivery slot settings",
    )
    return org


@store_router.patch("/profile", response_model=OrganizationOut)
def set_store_profile(
    payload: StoreProfileUpdate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin),
):
    """
    Admin-only: the store's marketplace listing details — a free-text
    category (used for the filter dropdown on the public /stores
    marketplace) and a short description shown on its store card. Both
    optional; either can be cleared by sending an empty string.
    """
    org = db.query(OrganizationDB).filter(OrganizationDB.id == current_user.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")
    if payload.category is not None:
        org.category = payload.category or None
    if payload.description is not None:
        org.description = payload.description or None
    db.commit()
    db.refresh(org)
    record_action(
        db, org_id=current_user.org_id,
        actor_user_id=current_user.id, actor_display_name=current_user.display_name,
        action="store_settings.update", entity_type="store_settings", entity_id=org.id,
        entity_label="Store profile",
        summary="Updated store marketplace profile",
    )
    return org
