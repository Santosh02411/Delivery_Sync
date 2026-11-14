"""
Where uploaded product images are stored, behind one small interface.

WHY THIS EXISTS
---------------
Images used to be written straight into a folder inside the application container. On a PaaS (Render,
Heroku, Fly...) the container's filesystem is EPHEMERAL: every deploy or restart starts from a clean
copy and every product image uploaded since the last deploy silently disappears (the product row still
points at it, so the shop shows broken images). Production needs storage that outlives the container.

TWO BACKENDS, chosen with UPLOAD_STORAGE
----------------------------------------
local (default)  Files on a disk. Fine for development. For production it is only safe if that folder is
                 a PERSISTENT DISK mounted into the container (see render.yaml / docker-compose.yml); point
                 UPLOAD_DIR at it. Without UPLOAD_DIR in production a loud warning is logged.
s3               Any S3-compatible object store (AWS S3, Cloudflare R2, MinIO, DigitalOcean Spaces...).
                 Survives deploys and scales to many app instances (a disk can only be attached to one).
                 Needs `boto3` (not installed by default) and the variables below.

S3 settings:  UPLOAD_S3_BUCKET (required), UPLOAD_PUBLIC_BASE_URL (required: the public https address the
              browser loads images from — a CDN or the bucket's public URL), UPLOAD_S3_ENDPOINT_URL (R2/MinIO),
              UPLOAD_S3_REGION, UPLOAD_S3_PREFIX (default "products"); credentials come from the usual AWS
              mechanisms (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, an instance role, ...).

SAFETY PROPERTIES (both backends)
---------------------------------
- Object names are RANDOM (128 bits from `secrets`), never derived from anything the client sent, so a
  client can't choose a path, overwrite another file, or guess a neighbour's file name.
- The extension comes from the format detected in the file's bytes (services/image_upload.py).
- Local writes are atomic and exclusive (temp file + O_EXCL + os.replace) with mode 0644, and every
  resolved path is checked to stay inside the upload root.
- delete() only ever acts on URLs this store itself would have produced (strict pattern), so a product
  whose image_url is an external link — or a crafted "/uploads/products/../../x" — can never make the
  server remove an arbitrary file or object.
- Access policy: the images are PUBLIC-READ by design (a shop front shows them), the app can only
  write/delete under the products prefix, and the files are served with `X-Content-Type-Options: nosniff`,
  a locked-down CSP and long-lived immutable caching (main.py). Bucket-side policy is in docs/UPLOADS_AND_STORAGE.md.
"""

import logging
import os
import re
import secrets
from typing import Optional

logger = logging.getLogger(__name__)

PRODUCTS_PREFIX = "products"
# The ONLY shape of name this module will ever delete: a random token + a known image extension.
# (The class also admits the dashes of the uuid4 names used before this change.)
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}\.(?:jpg|png|webp|gif)$")


def default_upload_root() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads")


def uploads_root() -> str:
    """The folder served at /uploads. UPLOAD_DIR points it at a persistent disk in production."""
    return os.path.abspath(os.environ.get("UPLOAD_DIR") or default_upload_root())


def new_object_name(extension: str) -> str:
    """e.g. 'q3J2x0m5eZ0L1k8u9rT4pA.jpg' — 128 random bits, nothing from the client."""
    return f"{secrets.token_urlsafe(16)}.{extension.lower().lstrip('.')}"


class ImageStore:
    def save(self, data: bytes, extension: str, content_type: str) -> str:  # returns the URL to store on the product
        raise NotImplementedError

    def delete(self, url: Optional[str]) -> bool:  # best effort; True if something was removed
        raise NotImplementedError


# --------------------------------------------------------------------------------------
# Local disk
# --------------------------------------------------------------------------------------

class LocalDiskStore(ImageStore):
    def __init__(self, root: Optional[str] = None):
        self.root = os.path.abspath(root or uploads_root())
        self.directory = os.path.join(self.root, PRODUCTS_PREFIX)
        os.makedirs(self.directory, exist_ok=True)

    def _safe_path(self, name: str) -> str:
        path = os.path.abspath(os.path.join(self.directory, name))
        if os.path.dirname(path) != os.path.abspath(self.directory):
            raise ValueError("unsafe object name")  # can't happen with generated names; belt and braces
        return path

    def save(self, data: bytes, extension: str, content_type: str) -> str:
        name = new_object_name(extension)
        final_path = self._safe_path(name)
        temp_path = self._safe_path(f".tmp-{secrets.token_hex(8)}")
        # O_EXCL: never write through an existing file or symlink. Temp + replace: readers never see a half-written image.
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, final_path)
        except BaseException:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise
        return f"/uploads/{PRODUCTS_PREFIX}/{name}"

    def delete(self, url: Optional[str]) -> bool:
        if not url or not url.startswith(f"/uploads/{PRODUCTS_PREFIX}/"):
            return False
        name = url[len(f"/uploads/{PRODUCTS_PREFIX}/"):]
        if not _NAME_RE.match(name):
            return False
        try:
            path = self._safe_path(name)
            if os.path.isfile(path) and not os.path.islink(path):
                os.remove(path)
                return True
        except (OSError, ValueError):
            pass
        return False


# --------------------------------------------------------------------------------------
# S3-compatible object storage
# --------------------------------------------------------------------------------------

class S3Store(ImageStore):
    CACHE_CONTROL = "public, max-age=31536000, immutable"  # names are random and never reused, so this is safe

    def __init__(self, bucket: str, public_base_url: str, prefix: str = PRODUCTS_PREFIX, client=None,
                 endpoint_url: Optional[str] = None, region: Optional[str] = None):
        if not bucket:
            raise RuntimeError("UPLOAD_S3_BUCKET is required when UPLOAD_STORAGE=s3.")
        if not public_base_url.lower().startswith("https://"):
            raise RuntimeError("UPLOAD_PUBLIC_BASE_URL must be an https:// address when UPLOAD_STORAGE=s3.")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.public_base_url = public_base_url.rstrip("/")
        if client is None:
            try:
                import boto3  # imported lazily: only needed when S3 is actually configured
            except ImportError as error:
                raise RuntimeError("UPLOAD_STORAGE=s3 needs the `boto3` package (pip install boto3).") from error
            client = boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region or None)
        self.client = client

    def save(self, data: bytes, extension: str, content_type: str) -> str:
        key = f"{self.prefix}/{new_object_name(extension)}"
        # No ACL parameter on purpose: modern buckets disable ACLs; public read is granted by the bucket
        # policy / CDN for the products prefix only (docs/UPLOADS_AND_STORAGE.md), not per object.
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
            CacheControl=self.CACHE_CONTROL, ContentDisposition="inline",
        )
        return f"{self.public_base_url}/{key}"

    def delete(self, url: Optional[str]) -> bool:
        if not url:
            return False
        base = f"{self.public_base_url}/{self.prefix}/"
        if not url.startswith(base):
            return False  # an external link or another prefix: never ours to delete
        name = url[len(base):]
        if not _NAME_RE.match(name):
            return False
        try:
            self.client.delete_object(Bucket=self.bucket, Key=f"{self.prefix}/{name}")
            return True
        except Exception:  # noqa: BLE001 - cleanup must never block deleting the product itself
            logger.warning("Could not delete object %s from the image store.", name, exc_info=True)
            return False


# --------------------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------------------

_store: Optional[ImageStore] = None


def build_store_from_env() -> ImageStore:
    mode = os.environ.get("UPLOAD_STORAGE", "local").strip().lower()
    production = os.environ.get("ENVIRONMENT", "development").strip().lower() == "production"
    if mode == "s3":
        return S3Store(
            bucket=os.environ.get("UPLOAD_S3_BUCKET", ""),
            public_base_url=os.environ.get("UPLOAD_PUBLIC_BASE_URL", ""),
            prefix=os.environ.get("UPLOAD_S3_PREFIX", PRODUCTS_PREFIX),
            endpoint_url=os.environ.get("UPLOAD_S3_ENDPOINT_URL"),
            region=os.environ.get("UPLOAD_S3_REGION"),
        )
    if mode != "local":
        raise RuntimeError(f"Unknown UPLOAD_STORAGE '{mode}' (use 'local' or 's3').")
    if production and not os.environ.get("UPLOAD_DIR"):
        logger.warning(
            "Product images are being stored in the application container's own filesystem. On most hosts that is "
            "wiped on every deploy/restart, so uploaded images WILL be lost. Set UPLOAD_STORAGE=s3, or mount a "
            "persistent disk and point UPLOAD_DIR at it (see docs/UPLOADS_AND_STORAGE.md)."
        )
    return LocalDiskStore()


def get_image_store() -> ImageStore:
    global _store
    if _store is None:
        _store = build_store_from_env()
    return _store


def set_image_store(store: Optional[ImageStore]) -> None:
    """For tests (and anything that needs to swap the backend)."""
    global _store
    _store = store
