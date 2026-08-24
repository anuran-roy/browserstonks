"""Managed storage upload for generated content (doc.create etc.).

Providers (config: `storage`):
    cloudinary (default) — unsigned/signed upload via REST API
    s3                  — AWS S3 PUT with presigned-style headers
    gcs                 — Google Cloud Storage upload endpoint

Each provider needs credentials in .env (see .env.example):
    CLOUDINARY_CLOUD_NAME / CLOUDINARY_API_KEY / CLOUDINARY_API_SECRET
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_S3_BUCKET / AWS_REGION
    GCS_BUCKET / GCS_ACCESS_TOKEN (or GOOGLE_APPLICATION_CREDENTIALS json)

`upload(path, session_id)` returns the public URL of the uploaded object,
or None when storage isn't configured (the local media/ copy is always
written first — upload is a distribution step, not the source of truth).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .logging_util import log as _log

_st_log = _log("storage")

CONTENT_TYPES = {
    ".html": "text/html", ".md": "text/markdown", ".txt": "text/plain",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".pdf": "application/pdf", ".json": "application/json",
}


class StorageError(Exception):
    pass


# ---------------------------------------------------------------- cloudinary
def _upload_cloudinary(path: Path, key: str, cfg: dict[str, str]) -> str:
    """Signed upload to Cloudinary's REST API (no SDK needed)."""
    cloud = cfg.get("cloud_name", "")
    if not cloud:
        raise StorageError("CLOUDINARY_CLOUD_NAME not set")
    api_key = cfg.get("api_key", "")
    api_secret = cfg.get("api_secret", "")
    if not api_key or not api_secret:
        raise StorageError("CLOUDINARY_API_KEY / CLOUDINARY_API_SECRET not set")

    ts = str(int(time.time()))
    folder = f"browserstonks/{key}"
    # signature: folder+timestamp (params sorted alphabetically)
    to_sign = f"folder={folder}&timestamp={ts}{api_secret}"
    signature = hashlib.sha1(to_sign.encode()).hexdigest()  # noqa: S324 (cloudinary spec)

    boundary = "bs" + hashlib.md5(str(time.time()).encode()).hexdigest()  # noqa: S324
    body = io_encode_multipart(boundary, [
        ("folder", folder), ("timestamp", ts),
        ("api_key", api_key), ("signature", signature),
    ], file_field="file", file_path=path)
    url = (f"https://api.cloudinary.com/v1_1/{cloud}/auto/upload")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    _st_log.info("cloudinary upload ok: %s", data.get("secure_url", ""))
    return data["secure_url"]


def io_encode_multipart(boundary: str, fields: list[tuple[str, str]],
                        file_field: str, file_path: Path) -> bytes:
    out = bytearray()
    for name, value in fields:
        out += (f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="{name}"\r\n\r\n{value}\r\n').encode()
    out += (f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="{file_field}"; filename="{file_path.name}"\r\n'
            f"Content-Type: {CONTENT_TYPES.get(file_path.suffix.lower(), 'application/octet-stream')}"
            f"\r\n\r\n").encode()
    out += file_path.read_bytes()
    out += f"\r\n--{boundary}--\r\n".encode()
    return bytes(out)


# ----------------------------------------------------------------------- s3
def _upload_s3(path: Path, key: str, cfg: dict[str, str]) -> str:
    """AWS S3 PUT with SigV4 (raw implementation, no boto)."""
    bucket = cfg.get("bucket", "")
    region = cfg.get("region", "us-east-1")
    access = cfg.get("access_key", "")
    secret = cfg.get("secret_key", "")
    if not (bucket and access and secret):
        raise StorageError("AWS_S3_BUCKET / AWS_ACCESS_KEY_ID / "
                           "AWS_SECRET_ACCESS_KEY not set")

    host = f"{bucket}.s3.{region}.amazonaws.com"
    url = f"https://{host}/{key}"
    payload = path.read_bytes()
    ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    amz_date = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    date_stamp = time.strftime("%Y%m%d", time.gmtime())
    payload_hash = hashlib.sha256(payload).hexdigest()

    canonical = (f"PUT\n/{key}\n\ncontent-type:{ctype}\nhost:{host}\n"
                 f"x-amz-content-sha256:{payload_hash}\n"
                 f"x-amz-date:{amz_date}\n\n"
                 f"content-type;host;x-amz-content-sha256;x-amz-date\n"
                 f"{payload_hash}")
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    to_sign = (f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n"
               + hashlib.sha256(canonical.encode()).hexdigest())

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k = _hmac(("AWS4" + secret).encode(), date_stamp)
    k = _hmac(k, region)
    k = _hmac(k, "s3")
    k = _hmac(k, "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()

    auth = (f"AWS4-HMAC-SHA256 Credential={access}/{scope}, "
            f"SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date, "
            f"Signature={sig}")
    req = urllib.request.Request(url, data=payload, method="PUT", headers={
        "Authorization": auth, "Content-Type": ctype,
        "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash})
    with urllib.request.urlopen(req, timeout=60) as resp:
        _st_log.info("s3 upload ok: %d -> %s", resp.status, url)
    return url


# ----------------------------------------------------------------------- gcs
def _upload_gcs(path: Path, key: str, cfg: dict[str, str]) -> str:
    """GCS XML API upload with an OAuth access token."""
    bucket = cfg.get("bucket", "")
    token = cfg.get("access_token", "")
    if not (bucket and token):
        raise StorageError("GCS_BUCKET / GCS_ACCESS_TOKEN not set")
    url = (f"https://storage.googleapis.com/upload/storage/v1/b/"
           f"{bucket}/o?uploadType=media&name="
           f"{urllib.parse.quote(key)}")
    payload = path.read_bytes()
    ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    public = f"https://storage.googleapis.com/{bucket}/{key}"
    _st_log.info("gcs upload ok: %s", public)
    return public


PROVIDERS = {
    "cloudinary": _upload_cloudinary,
    "s3": _upload_s3,
    "gcs": _upload_gcs,
}


class StorageManager:
    """Uploads generated content to the configured managed storage."""

    def __init__(self, config: dict[str, Any], env: dict[str, str]) -> None:
        self.enabled = bool(config.get("enabled", False))
        self.provider = config.get("provider", "cloudinary")
        env = env or {}
        if self.provider == "cloudinary":
            self.cfg = {
                "cloud_name": env.get("CLOUDINARY_CLOUD_NAME", ""),
                "api_key": env.get("CLOUDINARY_API_KEY", ""),
                "api_secret": env.get("CLOUDINARY_API_SECRET", ""),
            }
        elif self.provider == "s3":
            self.cfg = {
                "bucket": env.get("AWS_S3_BUCKET", ""),
                "region": env.get("AWS_REGION", "us-east-1"),
                "access_key": env.get("AWS_ACCESS_KEY_ID", ""),
                "secret_key": env.get("AWS_SECRET_ACCESS_KEY", ""),
            }
        elif self.provider == "gcs":
            self.cfg = {
                "bucket": env.get("GCS_BUCKET", ""),
                "access_token": env.get("GCS_ACCESS_TOKEN", ""),
            }
        else:
            self.cfg = {}
            _st_log.warning("unknown storage provider '%s'", self.provider)

    def upload(self, path: Path, session_id: str = "primary") -> str | None:
        """Upload the file; returns its public URL or None when disabled."""
        if not self.enabled:
            return None
        fn = PROVIDERS.get(self.provider)
        if fn is None:
            return None
        key = f"browserstonks/{_SAFE.sub('_', session_id)}/{path.name}"
        try:
            return fn(path, key, self.cfg)
        except StorageError as e:
            _st_log.warning("storage upload skipped: %s", e)
            return None
        except Exception as e:  # noqa: BLE001 - uploads must never kill turns
            _st_log.warning("storage upload failed: %s", e)
            return None


_SAFE = __import__("re").compile(r"[^A-Za-z0-9_.-]+")
