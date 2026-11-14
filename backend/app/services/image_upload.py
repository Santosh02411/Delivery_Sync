"""
Safe handling of uploaded product images.

THE PROBLEM
-----------
The upload endpoint trusted the `Content-Type` the client *declared* (trivially forged: a script or an
HTML page can be sent as `image/jpeg`), read the whole body into memory BEFORE checking the 5 MB limit
(so an oversized upload used memory before being refused), and never looked inside the file (a tiny file
that claims to be 60000x60000 pixels — a "decompression bomb" — would need gigabytes of memory to decode
once a browser or an image pipeline touched it).

WHAT THIS MODULE DOES
---------------------
1. read_upload_bounded()  reads in small chunks and STOPS the moment the limit is crossed, so memory
   use is bounded by the limit (plus one chunk) no matter how much the client sends.
2. sanitize_image()       decides what the file really is from its BYTES, never from the declared
   MIME type or the file name:
     - magic-number check, then Pillow restricted to JPEG / PNG / GIF / WebP (so other Pillow plugins —
       TIFF, BMP, ICO, PSD, EPS... — can't be reached through a lying upload);
     - dimensions and pixel count are read from the HEADER and checked BEFORE any pixel is decoded
       (decompression-bomb defence), plus a frame cap and a total-pixel budget for animated images;
     - the image is then fully decoded, so truncated / corrupt files are refused here rather than
       breaking someone's browser later;
     - single-frame JPEG / PNG / WebP are RE-ENCODED from the decoded pixels. That drops all embedded
       metadata (EXIF GPS locations of the dispatcher's phone, comments, ICC blobs), normalizes the file,
       and destroys "polyglot" files (a valid image with a script appended). Animated images (GIF,
       animated WebP/APNG) are fully decoded and validated but stored unchanged, because re-encoding
       animation reliably is error-prone.
   The stored extension and content type come from the DETECTED format.

Pure Python + Pillow: no web-framework imports, so it is unit-tested directly (tests/test_image_upload.py).
"""

import io
import os
import warnings
from dataclasses import dataclass
from typing import Awaitable, Callable

from PIL import Image, ImageOps, ImageSequence

# --------------------------------------------------------------------------------------
# Limits (all overridable by environment variable)
# --------------------------------------------------------------------------------------


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


MAX_IMAGE_BYTES = _int_env("PRODUCT_IMAGE_MAX_BYTES", 5 * 1024 * 1024)   # upload size
MAX_IMAGE_SIDE = _int_env("PRODUCT_IMAGE_MAX_SIDE", 6000)                # longest side, pixels
MAX_IMAGE_PIXELS = _int_env("PRODUCT_IMAGE_MAX_PIXELS", 16_000_000)      # width * height (16 MP ≈ 48 MB as RGB)
MAX_FRAMES = _int_env("PRODUCT_IMAGE_MAX_FRAMES", 60)                    # animated images
MAX_TOTAL_PIXELS = _int_env("PRODUCT_IMAGE_MAX_TOTAL_PIXELS", 60_000_000)  # width * height * frames
READ_CHUNK_BYTES = 64 * 1024

# Pillow's own guard, tightened to OUR limit: anything over it raises DecompressionBombWarning/Error.
# (We check the header ourselves first, so this is the second wall, not the first.)
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

ALLOWED_FORMATS = ("JPEG", "PNG", "GIF", "WEBP")
_FORMAT_INFO = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "GIF": ("image/gif", "gif"),
    "WEBP": ("image/webp", "webp"),
}


class ImageRejected(Exception):
    """The upload is refused. `status` is the HTTP status to answer with, `code` is machine-readable."""

    def __init__(self, message: str, code: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


@dataclass(frozen=True)
class SanitizedImage:
    data: bytes          # what should be stored
    content_type: str    # from the DETECTED format
    extension: str       # without the dot; from the DETECTED format
    width: int
    height: int
    frames: int
    reencoded: bool      # False for animated images, which are validated but stored unchanged


# --------------------------------------------------------------------------------------
# 1. Bounded reading
# --------------------------------------------------------------------------------------

async def read_upload_bounded(
    read: Callable[[int], Awaitable[bytes]],
    max_bytes: int = MAX_IMAGE_BYTES,
    chunk_size: int = READ_CHUNK_BYTES,
) -> bytes:
    """
    Reads from `read(n)` (e.g. UploadFile.read) in chunks and raises ImageRejected(413) the moment
    more than `max_bytes` have arrived — it never keeps reading to "see how big it is". Peak memory
    is therefore max_bytes + one chunk, whatever the client sends.
    """
    buffer = bytearray()
    while True:
        chunk = await read(chunk_size)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise ImageRejected(
                f"Image is too large — max {max_bytes // (1024 * 1024) or 1} MB.", "too_large", status=413
            )
    if not buffer:
        raise ImageRejected("That file is empty.", "empty")
    return bytes(buffer)


# --------------------------------------------------------------------------------------
# 2. Inspect, decode, sanitize
# --------------------------------------------------------------------------------------

def _looks_like_allowed_image(data: bytes) -> bool:
    """Cheap magic-number gate before Pillow is involved at all."""
    return (
        data.startswith(b"\xff\xd8\xff")                       # JPEG
        or data.startswith(b"\x89PNG\r\n\x1a\n")               # PNG
        or data.startswith((b"GIF87a", b"GIF89a"))             # GIF
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")     # WebP
    )


def _check_dimensions(width: int, height: int, frames: int) -> None:
    if width <= 0 or height <= 0:
        raise ImageRejected("The image has no usable dimensions.", "bad_dimensions")
    if max(width, height) > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS:
        raise ImageRejected(
            f"Image dimensions are too large ({width}x{height}). Max {MAX_IMAGE_SIDE}px on the longest side "
            f"and {MAX_IMAGE_PIXELS // 1_000_000} megapixels.",
            "dimensions_too_large",
        )
    if frames > MAX_FRAMES:
        raise ImageRejected(f"Animated image has too many frames ({frames}; max {MAX_FRAMES}).", "too_many_frames")
    if width * height * frames > MAX_TOTAL_PIXELS:
        raise ImageRejected("Animated image is too large to process safely.", "dimensions_too_large")


def _encode(image: Image.Image, fmt: str) -> bytes:
    out = io.BytesIO()
    if fmt == "JPEG":
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")  # CMYK / RGBA / palette -> RGB
        image.save(out, format="JPEG", quality=88, optimize=True)
    elif fmt == "PNG":
        image.save(out, format="PNG", optimize=True)
    else:  # WEBP
        image.save(out, format="WEBP", quality=90, method=4)
    return out.getvalue()


def sanitize_image(data: bytes) -> SanitizedImage:
    """
    Returns the image to store, or raises ImageRejected. CPU-bound and synchronous: call it from a
    worker thread (see routes/products.py), not directly on the event loop.
    """
    if not data:
        raise ImageRejected("That file is empty.", "empty")
    if not _looks_like_allowed_image(data):
        raise ImageRejected("That file is not a JPEG, PNG, WebP or GIF image.", "not_an_image")

    try:
        with warnings.catch_warnings():
            # Pillow only WARNS between 1x and 2x its pixel limit; for an upload endpoint that is a rejection.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            try:
                image = Image.open(io.BytesIO(data), formats=list(ALLOWED_FORMATS))
            except (Image.DecompressionBombError, Image.DecompressionBombWarning):
                raise ImageRejected("Image dimensions are too large.", "dimensions_too_large")

            fmt = image.format
            if fmt not in _FORMAT_INFO:
                raise ImageRejected("That file is not a JPEG, PNG, WebP or GIF image.", "not_an_image")

            width, height = image.size                    # read from the header: nothing decoded yet
            frames = int(getattr(image, "n_frames", 1) or 1)
            _check_dimensions(width, height, frames)

            content_type, extension = _FORMAT_INFO[fmt]

            if frames > 1 or fmt == "GIF":
                # Animated (or GIF): decode EVERY frame so a corrupt/truncated file can't slip through,
                # then store the original bytes unchanged.
                for frame in ImageSequence.Iterator(image):
                    frame.load()
                return SanitizedImage(data, content_type, extension, width, height, frames, reencoded=False)

            image.load()  # full decode: raises on truncated / corrupt data
            if fmt == "JPEG":
                image = ImageOps.exif_transpose(image)  # bake the camera's rotation in before the EXIF is dropped
            clean = _encode(image, fmt)
    except ImageRejected:
        raise
    except Exception:  # noqa: BLE001 - Pillow raises OSError, SyntaxError, ValueError, EOFError, struct.error...
        raise ImageRejected("That file is damaged or is not a valid image.", "malformed")

    if len(clean) > MAX_IMAGE_BYTES:
        raise ImageRejected("Image is too large — max 5 MB.", "too_large", status=413)
    return SanitizedImage(clean, content_type, extension, width, height, frames, reencoded=True)
