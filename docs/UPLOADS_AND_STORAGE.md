# Uploaded files: validation, storage and access

Covers product image uploads (`POST /admin/products/upload-image`). Code: `backend/app/services/image_upload.py`,
`object_storage.py`, `upload_limits.py`, `routes/products.py`, and the static serving in `main.py`.

## What a hostile or careless upload can no longer do

| Attack / mistake | Before | Now |
|---|---|---|
| Send `Content-Type: image/jpeg` on an HTML/PHP/SVG/exe file | accepted (only the declared type was checked) | refused: the **bytes** decide (magic number, then Pillow restricted to JPEG/PNG/GIF/WebP) |
| Send a 2 GB body | the whole body was read into memory, *then* refused | refused with **413 before parsing** (Content-Length, or counted as it streams); the route reads in 64 KB chunks and stops at the limit |
| Send a small file that expands to gigapixels (decompression bomb) | accepted; blew up whoever decoded it later | dimensions/pixels read from the **header** and refused before any pixel is decoded (max 6000 px side, 16 MP); frame and total-pixel budgets for animations; Pillow's own bomb guard as a second wall |
| Send a truncated / corrupt image | accepted and stored | fully decoded; refused as damaged |
| Hide a script after a valid image (polyglot) | stored as-is | JPEG/PNG/WebP are **re-encoded** from decoded pixels, which drops anything appended |
| Leak the phone's GPS location in EXIF | stored as-is | metadata stripped (rotation is baked into the pixels first) |
| Choose the file name / extension | name derived from the client's, extension from the declared type | name is 128 random bits; extension from the **detected** format; client name ignored |
| Upload anonymously to burn bandwidth | body was parsed before auth was checked | no `Authorization` header → **401 before any body is read** |
| Delete an arbitrary file via a crafted `image_url` | cleanup trusted the URL | delete only acts on URLs this store produced (strict name pattern; never follows symlinks) |

Animated images (GIF, animated WebP/APNG) are fully decoded and validated but stored unchanged. GIFs are never re-encoded.

## Access policy

- Product images are **public-read by design** (a shop front shows them). Everything else under `/uploads` is served by the general mount.
- Served with `X-Content-Type-Options: nosniff`, a CSP of `default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox`
  (so an image opened directly can't run script even if something went wrong upstream), and for product images
  `Cache-Control: public, max-age=31536000, immutable` — safe because names are random and never reused.
- The application only ever writes/deletes under the `products/` prefix.

## Durable storage (important for production)

A container's filesystem is **wiped on every deploy and restart** on Render and similar hosts. Before this change, every
product image uploaded since the last deploy disappeared while the product kept pointing at it. Pick one:

**A. Persistent disk (simplest).** `render.yaml` now attaches a 5 GB disk at `/var/data` and sets `UPLOAD_DIR=/var/data/uploads`;
`docker-compose.yml` mounts a named volume at `/data/uploads`. Support attachments and finance PDFs use the same root.
Limits: a Render disk attaches to **one** instance only (no horizontal scaling) and cannot do zero-downtime deploys.
If `ENVIRONMENT=production`, local storage, and `UPLOAD_DIR` is unset, a loud warning is logged at startup.

**B. Object storage (scales, survives anything).** Install `boto3` (uncomment it in `requirements.txt`) and set
`UPLOAD_STORAGE=s3`, `UPLOAD_S3_BUCKET`, `UPLOAD_PUBLIC_BASE_URL` (+ `UPLOAD_S3_ENDPOINT_URL` for R2/MinIO) and credentials. Only **product
images** move to object storage in this change; support attachments and finance PDFs stay on the disk (A), so keep the disk if you use them.

Recommended bucket setup (S3 shown; R2/Spaces are equivalent):
1. Block *public ACLs*; keep ACLs disabled (the app never sets one).
2. Serve images through a CDN (CloudFront/Cloudflare) or grant public `s3:GetObject` on **`products/*` only**.
3. Give the app's credentials only `s3:PutObject` and `s3:DeleteObject` on `arn:aws:s3:::BUCKET/products/*` — no list, no read of other prefixes.
4. Add a lifecycle rule to abort incomplete multipart uploads; enable bucket default encryption.

**Existing images:** files already on a non-persistent filesystem are gone; products pointing at them need re-upload. Existing
`/uploads/products/<uuid>.<ext>` URLs keep working when the folder is persisted, and old names are still deletable.

## Tuning
`PRODUCT_IMAGE_MAX_BYTES` (5 MB), `PRODUCT_IMAGE_MAX_SIDE` (6000), `PRODUCT_IMAGE_MAX_PIXELS` (16,000,000),
`PRODUCT_IMAGE_MAX_FRAMES` (60), `PRODUCT_IMAGE_MAX_TOTAL_PIXELS` (60,000,000). Decoding runs in a worker thread, two at a time,
so a burst of uploads queues instead of exhausting memory (a 16 MP RGB image needs ~48 MB to decode).

## Not covered
Support-ticket attachments (`routes/support.py`) and generated finance PDFs are separate upload paths with their own rules; they were
not changed except to follow `UPLOAD_DIR`. Malware scanning is not performed (images are re-encoded instead, which removes most
payload-smuggling, but it is not antivirus).
