# Direct uploads

!!! tip "Reference"

    [configuration reference](../options/contrib.md#uploads-ninja_devxcontribuploads)

Large files should not pass through your API processes. With presigned uploads, the API
checks the request and signs it, the client uploads straight to S3 (or an S3-compatible
store that supports POST uploads, such as MinIO), and the API then confirms what was stored.

```bash
pip install "ninja-devx[s3]"
```

```python
from ninja_devx.contrib.uploads import S3Signer, UploadController, UploadPolicy


class AvatarUploads(UploadController):
    signer = S3Signer(bucket="media", prefix="uploads/")
    policy = UploadPolicy(
        content_types=("image/png", "image/jpeg", "image/webp"), max_bytes=2_000_000
    )
    key_prefix = "avatars/"


mount(api, {"/uploads/avatars": AvatarUploads})
```

## The flow

1. **Sign.** `POST /uploads/avatars/` with `{"filename": "me.png", "content_type": "image/png", "size": 48213}`:

    ```json
    {
      "url": "https://media.s3.amazonaws.com/",
      "fields": {"key": "uploads/avatars/42/9f1c.../me.png", "Content-Type": "image/png", "policy": "...", "x-amz-signature": "..."},
      "key": "uploads/avatars/42/9f1c.../me.png",
      "expires_at": "2026-01-01T00:10:00Z"
    }
    ```

    Unsupported types get 415, and sizes over `max_bytes` get 413.

2. **Upload.** The client sends `multipart/form-data` to `url` with every entry of
   `fields`, then `file`. The storage service enforces the content type and the size
   range, so a client cannot upload more than the policy allows.

    ```js
    const form = new FormData();
    Object.entries(signed.fields).forEach(([k, v]) => form.append(k, v));
    form.append("file", file);
    await fetch(signed.url, { method: "POST", body: form });
    ```

3. **Confirm.** `POST /uploads/avatars/complete` with `{"key": ...}` returns
   `key`, `size`, `content_type`, `checksum_sha256`, `version_id` and `etag`, read from storage.
   It answers 404 for missing/unissued keys or another owner, 410 for expired authorization,
   and 422 for a size/type/checksum mismatch. Repeating completion returns the same metadata;
   a changed completed version/ETag returns 409. Save the key **and version ID** on your model.

## Keys and safety

- Keys are `<signer prefix><key_prefix><user id>/<random uuid>/<safe filename>`. Clients
  cannot choose the path or confirm someone else's upload. A still-valid signed form can
  overwrite its own key; use versioning and read the returned version ID to pin verified bytes.
- File names are reduced to ASCII letters, digits, `.`, `_` and `-` (`safe_filename`).
- Signed forms expire after `UploadPolicy.expires_in` (10 minutes by default).
- Override `owner_prefix(request)` to group uploads by tenant instead of user.
- Treat uploaded files as untrusted. Serve them from a separate domain, with
  `Content-Disposition: attachment` for anything that isn't an image.

## Other storage

A signer implements `presign(key, *, content_type, max_bytes, expires_in, checksum_sha256=None)`,
`stat(key)`, and `delete(key, *, version_id=None)`. `S3Signer(client=...)` accepts a configured boto3 client, for example for MinIO:

```python
S3Signer(bucket="media", client=boto3.client("s3", endpoint_url="http://minio:9000"))
```

## Testing

```python
from ninja_devx.contrib.uploads import FakeSigner

signer = FakeSigner()


class TestUploads(AvatarUploads):
    signer = signer


def test_upload(ninja_client, user):
    client = ninja_client(TestUploads)
    key = client.post(
        "/", json={"filename": "a.png", "content_type": "image/png", "size": 10}, user=user
    ).json()["key"]
    signer.store(key, size=10, content_type="image/png")  # what the browser would do
    assert client.post("/complete", json={"key": key}, user=user).status_code == 200
```

## Durable completion and cleanup

Install `ninja_devx` and run migrations on `UploadController.upload_database` (default:
`default`). Signing stores a pending authorization tied to the owner, controller namespace,
exact expected byte count, content type, checksum and expiry. Completion locks this record
and marks it complete only after storage metadata matches. A matching key prefix alone
is insufficient authorization.

For content integrity and immutable references, enable both checks:

```python
class VerifiedUploads(AvatarUploads):
    policy = UploadPolicy(
        content_types=("image/png",), max_bytes=2_000_000,
        require_checksum=True, require_version=True,
    )
```

Clients send `checksum_sha256` as base64 SHA-256 with the signing request. S3 POST policy
binds that checksum, and completion checks the value returned by HEAD. The store must
support checksum-aware HEAD and have bucket versioning enabled when required. A checksum
verifies bytes, not file safety or MIME magic; perform application-specific inspection
before serving sensitive upload types. Without versioning, a returned key is not an
immutable reference. Without a requested checksum, only the configured metadata checks
are guaranteed.

Run bounded cleanup from a job or management command:

```bash
python manage.py devx_uploads myapp.api.AvatarUploads --limit 100
```

`AvatarUploads().cleanup_expired(limit=100)` removes the current storage object for expired
pending records after `policy.cleanup_grace` (15 minutes by default), then marks those
records expired. Completed and live authorizations are preserved. Storage failures leave
the record retryable. Configure a bucket lifecycle rule for noncurrent versions and
abandoned multipart uploads; this command does not enumerate arbitrary bucket objects.
Use a grace longer than the maximum upload duration allowed by your ingress/storage.

A custom `owner_prefix` should include both tenant and user when tenants contain users
who must not confirm one another's uploads. S3 client credentials and bucket access are
configured by the application; no storage keys are embedded in generated clients.
