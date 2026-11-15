"""
Tests for hardened product image uploads:

  services/image_upload.py   bounded reading, real-format detection, decode, decompression-bomb limits,
                             metadata stripping
  services/object_storage.py random names, safe writes, delete only ever acts on our own URLs, S3 backend
  services/upload_limits.py  oversized / anonymous bodies refused BEFORE they are parsed
  routes/products.py         end to end: spoofed type, malformed, oversized, huge dimensions, valid image

Nothing here touches the network: S3 is a recording fake, and "huge images" are tiny crafted files whose
HEADER claims enormous dimensions (that is exactly what a decompression bomb is).
"""

import asyncio
import io
import json
import os
import struct
import zlib

import pytest
from PIL import Image

from app.services import image_upload as iu
from app.services.image_upload import ImageRejected, read_upload_bounded, sanitize_image
from app.services.object_storage import LocalDiskStore, S3Store, new_object_name
from app.services.upload_limits import BodySizeLimitMiddleware


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------

def make_image(fmt="JPEG", size=(40, 30), mode="RGB", **save_kwargs) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, (200, 10, 10) if mode != "L" else 90).save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def png_claiming(width: int, height: int) -> bytes:
    """A VALID 1-bit PNG of the given dimensions made of zeros. It compresses to a few dozen KB even when it
    describes hundreds of megapixels — which is what makes decompression bombs dangerous."""
    row = b"\x00" + b"\x00" * ((width + 7) // 8)
    compressor = zlib.compressobj(9)
    data = b"".join(compressor.compress(row) for _ in range(height)) + compressor.flush()
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0))
            + _png_chunk(b"IDAT", data) + _png_chunk(b"IEND", b""))


def reject_code(data: bytes) -> str:
    with pytest.raises(ImageRejected) as exc:
        sanitize_image(data)
    return exc.value.code


class ChunkSource:
    """Stands in for UploadFile.read(n): offers `total` bytes and records how much was actually pulled."""

    def __init__(self, total: int):
        self.left = total
        self.pulled = 0

    async def read(self, n: int) -> bytes:
        take = min(n, self.left)
        self.left -= take
        self.pulled += take
        return b"x" * take


# --------------------------------------------------------------------------------------
# Bounded reading
# --------------------------------------------------------------------------------------

def test_oversized_upload_is_refused_without_reading_the_rest():
    source = ChunkSource(total=200 * 1024 * 1024)  # the client keeps offering 200 MB
    with pytest.raises(ImageRejected) as exc:
        asyncio.run(read_upload_bounded(source.read, max_bytes=5 * 1024 * 1024))
    assert (exc.value.code, exc.value.status) == ("too_large", 413)
    assert source.pulled <= 5 * 1024 * 1024 + iu.READ_CHUNK_BYTES   # stopped at the limit + one chunk, not 200 MB


def test_upload_exactly_at_the_limit_is_accepted():
    source = ChunkSource(total=1024)
    assert len(asyncio.run(read_upload_bounded(source.read, max_bytes=1024))) == 1024


def test_one_byte_over_the_limit_is_refused():
    source = ChunkSource(total=1025)
    with pytest.raises(ImageRejected):
        asyncio.run(read_upload_bounded(source.read, max_bytes=1024))


def test_empty_upload_is_refused():
    with pytest.raises(ImageRejected) as exc:
        asyncio.run(read_upload_bounded(ChunkSource(0).read, max_bytes=1024))
    assert exc.value.code == "empty"


# --------------------------------------------------------------------------------------
# Real format detection (spoofed types)
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("fmt, content_type, extension", [
    ("JPEG", "image/jpeg", "jpg"), ("PNG", "image/png", "png"), ("WEBP", "image/webp", "webp"), ("GIF", "image/gif", "gif"),
])
def test_genuine_images_are_accepted_with_type_and_extension_from_their_contents(fmt, content_type, extension):
    result = sanitize_image(make_image(fmt))
    assert (result.content_type, result.extension, result.width, result.height) == (content_type, extension, 40, 30)


@pytest.mark.parametrize("name, payload", [
    ("html", b"<html><body><script>alert(document.cookie)</script></body></html>"),
    ("php", b"<?php system($_GET['c']); ?>"),
    ("svg with script", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script></svg>'),
    ("javascript", b"alert(1);"),
    ("windows exe", b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64),
    ("pdf", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"),
    ("zip", b"PK\x03\x04" + b"\x00" * 64),
])
def test_files_that_only_claim_to_be_images_are_refused(name, payload):
    """These are what a client sends with `Content-Type: image/jpeg` — the declared type means nothing."""
    assert reject_code(payload) == "not_an_image"


@pytest.mark.parametrize("fmt", ["BMP", "TIFF", "ICO"])
def test_real_images_in_formats_we_do_not_accept_are_refused(fmt):
    assert reject_code(make_image(fmt, size=(32, 32))) == "not_an_image"


def test_the_declared_name_and_type_play_no_part_the_bytes_decide():
    png_bytes = make_image("PNG")
    assert sanitize_image(png_bytes).extension == "png"   # whatever it was called, it is stored as a PNG


# --------------------------------------------------------------------------------------
# Malformed images
# --------------------------------------------------------------------------------------

def test_truncated_jpeg_is_refused():
    data = make_image("JPEG", size=(400, 400))
    assert reject_code(data[: len(data) // 2]) == "malformed"


def test_truncated_png_is_refused():
    data = make_image("PNG", size=(300, 300))
    assert reject_code(data[: len(data) // 2]) == "malformed"


def test_a_valid_header_followed_by_garbage_is_refused():
    assert reject_code(b"\xff\xd8\xff\xe0" + b"A" * 300) == "malformed"
    assert reject_code(b"\x89PNG\r\n\x1a\n" + b"B" * 300) == "malformed"
    assert reject_code(b"GIF89a" + b"C" * 300) == "malformed"
    assert reject_code(b"RIFF\x24\x00\x00\x00WEBP" + b"D" * 300) == "malformed"


def test_damaged_pixel_data_never_results_in_a_broken_stored_file():
    """Pillow does not verify PNG pixel-stream checksums, so a damaged-but-decodable PNG may be accepted — but
    because JPEG/PNG/WebP are RE-ENCODED from the decoded pixels, whatever is stored is always a clean, valid
    image. (Damage Pillow can't decode at all is refused as 'malformed'.)"""
    data = bytearray(make_image("PNG", size=(200, 200), mode="RGB"))
    mid = len(data) // 2
    data[mid:mid + 20] = b"\x00" * 20
    try:
        result = sanitize_image(bytes(data))
    except ImageRejected as error:
        assert error.code == "malformed"
    else:
        reopened = Image.open(io.BytesIO(result.data))
        reopened.load()                                  # decodes cleanly: nothing damaged was stored
        assert reopened.size == (200, 200)


def test_animated_gif_with_a_corrupt_later_frame_is_refused():
    frames = distinct_frames(5, size=(30, 30))
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=50)
    data = buffer.getvalue()
    assert reject_code(data[:-40]) == "malformed"      # chopped inside the last frame


# --------------------------------------------------------------------------------------
# Very large dimensions / decompression bombs
# --------------------------------------------------------------------------------------

def test_tiny_file_that_claims_hundreds_of_megapixels_is_refused_from_its_header():
    bomb = png_claiming(20000, 20000)                 # 400 megapixels
    assert len(bomb) < 5 * 1024 * 1024                # it WOULD pass a size check
    assert reject_code(bomb) == "dimensions_too_large"


def test_extremely_tall_or_wide_images_are_refused_even_with_few_pixels():
    assert reject_code(png_claiming(1, iu.MAX_IMAGE_SIDE + 1)) == "dimensions_too_large"
    assert reject_code(png_claiming(iu.MAX_IMAGE_SIDE + 1, 1)) == "dimensions_too_large"


def test_image_just_over_the_pixel_budget_is_refused_and_just_under_is_accepted():
    side = int(iu.MAX_IMAGE_PIXELS ** 0.5)
    assert reject_code(png_claiming(side + 50, side + 50)) == "dimensions_too_large"
    assert sanitize_image(png_claiming(800, 600)).width == 800


def distinct_frames(count, size=(8, 8)):
    """Pillow merges identical consecutive GIF frames, so each frame needs its own colour."""
    return [Image.new("RGB", size, ((i * 3) % 256, (i * 7) % 256, (i * 11) % 256)) for i in range(count)]


def test_animated_image_with_too_many_frames_is_refused():
    frames = distinct_frames(iu.MAX_FRAMES + 5)
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=10)
    assert reject_code(buffer.getvalue()) == "too_many_frames"


def test_animated_image_whose_frames_add_up_to_too_many_pixels_is_refused(monkeypatch):
    monkeypatch.setattr(iu, "MAX_TOTAL_PIXELS", 5_000)
    frames = distinct_frames(10, size=(40, 40))                 # 16,000 pixels in total
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=10)
    assert reject_code(buffer.getvalue()) == "dimensions_too_large"


# --------------------------------------------------------------------------------------
# Sanitizing (metadata, polyglots, rotation)
# --------------------------------------------------------------------------------------

def test_exif_metadata_including_gps_is_stripped_and_rotation_is_baked_in():
    exif = Image.Exif()
    exif[0x0112] = 6                                   # "rotate 90° clockwise to display"
    exif[0x010F] = "SecretPhoneMaker"
    exif[0x8825] = {1: "N", 2: (12.0, 58.0, 0.0), 3: "E", 4: (77.0, 35.0, 0.0)}   # GPS IFD
    buffer = io.BytesIO()
    Image.new("RGB", (60, 20), (1, 2, 3)).save(buffer, format="JPEG", exif=exif.tobytes())
    original = buffer.getvalue()
    assert b"SecretPhoneMaker" in original

    result = sanitize_image(original)

    assert b"SecretPhoneMaker" not in result.data and b"Exif" not in result.data
    stored = Image.open(io.BytesIO(result.data))
    assert dict(stored.getexif()) == {}
    assert stored.size == (20, 60)                     # the rotation the EXIF asked for is now in the pixels


def test_a_script_appended_to_a_valid_image_does_not_survive():
    polyglot = make_image("PNG") + b"<script>alert(1)</script>"
    result = sanitize_image(polyglot)
    assert b"<script>" not in result.data
    assert result.reencoded is True


def test_png_text_chunks_are_removed():
    info = __import__("PIL.PngImagePlugin", fromlist=["PngInfo"]).PngInfo()
    info.add_text("Comment", "tracking-id-1234")
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20)).save(buffer, format="PNG", pnginfo=info)
    assert b"tracking-id-1234" in buffer.getvalue()
    assert b"tracking-id-1234" not in sanitize_image(buffer.getvalue()).data


def test_cmyk_jpeg_is_converted_so_browsers_can_show_it():
    result = sanitize_image(make_image("JPEG", mode="CMYK"))
    assert Image.open(io.BytesIO(result.data)).mode in ("RGB", "L")


def test_animated_gif_is_validated_but_stored_unchanged():
    frames = [Image.new("RGB", (16, 16), (i * 40, 0, 0)) for i in range(4)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=40, loop=0)
    original = buffer.getvalue()
    result = sanitize_image(original)
    assert (result.reencoded, result.frames, result.data) == (False, 4, original)


# --------------------------------------------------------------------------------------
# Storage: random names, safe writes, safe deletes
# --------------------------------------------------------------------------------------

def test_stored_names_are_random_and_use_the_detected_extension(tmp_path):
    store = LocalDiskStore(str(tmp_path))
    urls = {store.save(b"data", "png", "image/png") for _ in range(50)}
    assert len(urls) == 50                                              # never reused
    for url in urls:
        assert url.startswith("/uploads/products/") and url.endswith(".png")
        assert len(os.path.basename(url)) > 20                          # 128 random bits, not a counter or the client's name
    assert new_object_name("jpg") != new_object_name("jpg")


def test_local_store_writes_atomically_with_safe_permissions_and_leaves_no_temp_files(tmp_path):
    store = LocalDiskStore(str(tmp_path))
    url = store.save(b"payload", "jpg", "image/jpeg")
    path = os.path.join(str(tmp_path), "products", os.path.basename(url))
    with open(path, "rb") as handle:
        assert handle.read() == b"payload"
    assert (os.stat(path).st_mode & 0o777) == 0o644
    assert [n for n in os.listdir(os.path.dirname(path)) if n.startswith(".tmp-")] == []


def test_delete_removes_our_own_files_only(tmp_path):
    store = LocalDiskStore(str(tmp_path))
    url = store.save(b"x", "jpg", "image/jpeg")
    outside = tmp_path / "secret.txt"
    outside.write_text("do not delete")
    bystander = tmp_path / "products" / "bystander-file-1234567890.png"
    bystander.write_bytes(b"keep")

    assert store.delete("https://example.com/products/whatever.jpg") is False      # external link
    assert store.delete("/uploads/products/../secret.txt") is False                # path traversal
    assert store.delete("/uploads/products/..%2Fsecret.txt") is False
    assert store.delete("/uploads/support/" + os.path.basename(url)) is False      # another folder
    assert store.delete("/uploads/products/not-a-random-name.exe") is False        # not our naming scheme/extension
    assert store.delete(None) is False
    assert outside.exists() and bystander.exists()

    assert store.delete(url) is True
    assert not os.path.exists(os.path.join(str(tmp_path), "products", os.path.basename(url)))


def test_delete_never_follows_a_symlink(tmp_path):
    store = LocalDiskStore(str(tmp_path))
    target = tmp_path / "precious.txt"
    target.write_text("precious")
    link = tmp_path / "products" / "aaaaaaaaaaaaaaaaaaaaaaaa.jpg"
    os.symlink(target, link)
    assert store.delete("/uploads/products/aaaaaaaaaaaaaaaaaaaaaaaa.jpg") is False
    assert target.exists()


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.deleted = []

    def put_object(self, **kwargs):
        self.objects[kwargs["Key"]] = kwargs

    def delete_object(self, Bucket, Key):
        self.deleted.append((Bucket, Key))
        self.objects.pop(Key, None)


def test_s3_store_uploads_with_random_key_content_type_and_caching_and_no_acl():
    client = FakeS3()
    store = S3Store("shop-images", "https://cdn.example.com/", client=client)
    url = store.save(b"img", "webp", "image/webp")

    (key, put), = client.objects.items()
    assert key.startswith("products/") and key.endswith(".webp") and len(key) > 30
    assert url == f"https://cdn.example.com/{key}"
    assert put["Bucket"] == "shop-images" and put["Body"] == b"img" and put["ContentType"] == "image/webp"
    assert "immutable" in put["CacheControl"] and put["ContentDisposition"] == "inline"
    assert "ACL" not in put                                                   # access is the bucket policy's job


def test_s3_store_deletes_only_its_own_objects():
    client = FakeS3()
    store = S3Store("shop-images", "https://cdn.example.com", client=client)
    url = store.save(b"img", "jpg", "image/jpeg")
    assert store.delete("https://evil.example.org/products/" + os.path.basename(url)) is False
    assert store.delete("https://cdn.example.com/other-prefix/" + os.path.basename(url)) is False
    assert store.delete("https://cdn.example.com/products/../x.jpg") is False
    assert client.deleted == []
    assert store.delete(url) is True and len(client.deleted) == 1


def test_s3_store_refuses_insecure_or_incomplete_configuration():
    with pytest.raises(RuntimeError):
        S3Store("", "https://cdn.example.com", client=FakeS3())
    with pytest.raises(RuntimeError):
        S3Store("bucket", "http://cdn.example.com", client=FakeS3())


# --------------------------------------------------------------------------------------
# Size-limit middleware (raw ASGI)
# --------------------------------------------------------------------------------------

def run_asgi(middleware, path="/admin/products/upload-image", method="POST", headers=(), body_chunks=(b"",)):
    sent = []
    pending = list(body_chunks)

    async def receive():
        if pending:
            chunk = pending.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(pending)}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path, "headers": list(headers)}
    asyncio.run(middleware(scope, receive, send))
    return sent


def make_app(consume=True, record=None):
    async def app(scope, receive, send):
        if consume:
            more = True
            total = 0
            while more:
                message = await receive()
                total += len(message.get("body", b""))
                more = message.get("more_body", False)
            if record is not None:
                record.append(total)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    return app


def status_of(sent):
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


AUTH = (b"authorization", b"Bearer t")


def test_middleware_refuses_a_declared_oversized_body_without_reading_it():
    seen = []
    mw = BodySizeLimitMiddleware(make_app(record=seen), {"/admin/products/upload-image": 1000})
    sent = run_asgi(mw, headers=[(b"content-length", b"999999999"), AUTH], body_chunks=[b"x" * 10])
    assert status_of(sent) == 413
    assert seen == []                                                    # the app was never even called
    assert b"too large" in sent[-1]["body"]


def test_middleware_cuts_off_a_chunked_body_that_lies_or_has_no_length():
    mw = BodySizeLimitMiddleware(make_app(), {"/admin/products/upload-image": 1000})
    sent = run_asgi(mw, headers=[AUTH], body_chunks=[b"x" * 600, b"x" * 600, b"x" * 600])
    assert status_of(sent) == 413
    sent = run_asgi(mw, headers=[(b"content-length", b"10"), AUTH], body_chunks=[b"x" * 5000])   # announces 10, sends 5000
    assert status_of(sent) == 413


def test_middleware_replaces_the_apps_own_error_with_413():
    """FastAPI turns a failed body read into its own 400; the client must still get the true reason."""
    async def app(scope, receive, send):
        try:
            while True:
                await receive()
        except Exception:
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"There was an error parsing the body"})
    mw = BodySizeLimitMiddleware(app, {"/admin/products/upload-image": 1000})
    sent = run_asgi(mw, headers=[AUTH], body_chunks=[b"x" * 600, b"x" * 600])
    assert [status_of(sent)] == [413] and b"parsing" not in b"".join(m.get("body", b"") for m in sent)


def test_middleware_lets_a_body_within_the_limit_through():
    seen = []
    mw = BodySizeLimitMiddleware(make_app(record=seen), {"/admin/products/upload-image": 1000})
    sent = run_asgi(mw, headers=[(b"content-length", b"900"), AUTH], body_chunks=[b"x" * 450, b"x" * 450])
    assert status_of(sent) == 200 and seen == [900]


def test_middleware_ignores_other_paths_and_methods():
    mw = BodySizeLimitMiddleware(make_app(), {"/admin/products/upload-image": 10})
    assert status_of(run_asgi(mw, path="/other", headers=[(b"content-length", b"5000")], body_chunks=[b"x" * 5000])) == 200
    assert status_of(run_asgi(mw, method="GET", headers=[(b"content-length", b"5000")])) == 200


def test_middleware_can_refuse_anonymous_uploads_before_reading_anything():
    seen = []
    mw = BodySizeLimitMiddleware(make_app(record=seen), {"/admin/products/upload-image": 1000}, require_authorization_header=True)
    sent = run_asgi(mw, headers=[(b"content-length", b"500")], body_chunks=[b"x" * 500])
    assert status_of(sent) == 401 and seen == []
    assert status_of(run_asgi(mw, headers=[(b"content-length", b"500"), AUTH], body_chunks=[b"x" * 500])) == 200


# --------------------------------------------------------------------------------------
# End to end through the real app (uses the project's `client` / `auth_headers` fixtures)
# --------------------------------------------------------------------------------------

UPLOAD = "/admin/products/upload-image"


def _post(client, headers, data, name="photo.jpg", content_type="image/jpeg"):
    return client.post(UPLOAD, files={"file": (name, data, content_type)}, headers=headers)


def _cleanup(url):
    from app.services.object_storage import get_image_store
    get_image_store().delete(url)


def test_valid_image_is_stored_under_a_random_name_and_served_safely(client, auth_headers):
    resp = _post(client, auth_headers, make_image("JPEG", size=(120, 80)))
    assert resp.status_code == 200, resp.text
    url = resp.json()["image_url"]
    try:
        assert url.startswith("/uploads/products/") and url.endswith(".jpg") and "photo" not in url
        served = client.get(url)
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/jpeg"
        assert served.headers["x-content-type-options"] == "nosniff"
        assert "script" not in served.headers["content-security-policy"].replace("default-src 'none'", "")
        assert "immutable" in served.headers["cache-control"]
        assert Image.open(io.BytesIO(served.content)).size == (120, 80)
    finally:
        _cleanup(url)


def test_a_html_page_sent_as_image_jpeg_is_refused(client, auth_headers):
    resp = _post(client, auth_headers, b"<html><script>alert(1)</script></html>", name="evil.jpg", content_type="image/jpeg")
    assert resp.status_code == 400
    assert "not a JPEG, PNG, WebP or GIF" in resp.json()["detail"]


def test_a_php_file_renamed_to_png_is_refused(client, auth_headers):
    assert _post(client, auth_headers, b"<?php echo 1; ?>", name="shell.png", content_type="image/png").status_code == 400


def test_a_real_image_with_a_wrong_declared_type_is_stored_by_what_it_really_is(client, auth_headers):
    resp = _post(client, auth_headers, make_image("PNG"), name="a.jpg", content_type="image/jpeg")
    assert resp.status_code == 200, resp.text
    try:
        assert resp.json()["image_url"].endswith(".png")
    finally:
        _cleanup(resp.json()["image_url"])


def test_a_malformed_image_is_refused(client, auth_headers):
    data = make_image("JPEG", size=(300, 300))
    resp = _post(client, auth_headers, data[: len(data) // 2])
    assert resp.status_code == 400 and "damaged" in resp.json()["detail"]


def test_a_decompression_bomb_is_refused_even_though_it_is_small(client, auth_headers):
    bomb = png_claiming(20000, 20000)
    assert len(bomb) < 1024 * 1024
    resp = _post(client, auth_headers, bomb, name="x.png", content_type="image/png")
    assert resp.status_code == 400 and "dimensions are too large" in resp.json()["detail"]


def test_an_oversized_upload_is_refused_with_413(client, auth_headers):
    resp = _post(client, auth_headers, b"\xff\xd8\xff" + b"0" * (6 * 1024 * 1024))
    assert resp.status_code == 413
    assert "too large" in resp.json()["detail"]


def test_an_anonymous_upload_is_refused_before_the_body_is_accepted(client):
    resp = client.post(UPLOAD, files={"file": ("a.jpg", make_image("JPEG"), "image/jpeg")})
    assert resp.status_code in (401, 403)


def test_uploading_and_then_deleting_a_product_removes_the_stored_image(client, auth_headers):
    url = _post(client, auth_headers, make_image("JPEG")).json()["image_url"]
    product = client.post("/admin/products/", json={"name": "Pic", "price": 5.0, "is_active": True, "image_url": url}, headers=auth_headers).json()
    assert client.get(url).status_code == 200
    assert client.delete(f"/admin/products/{product['id']}", headers=auth_headers).status_code == 200
    assert client.get(url).status_code == 404
