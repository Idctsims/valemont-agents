"""A minimal S3 client: HEAD and PUT one object, signed with SigV4. Stdlib only.

Written for one caller, the NFL props archive's storage mode
(docs/props_archive_storage.md), against Supabase Storage's S3 protocol,
path-style: ``{endpoint}/{bucket}/{key}``. No new dependency: boto3 would add
~15 MB to an image that never runs this, and two verbs do not need it.

What it does not do, on purpose: GET (the store-only rule: an archived object
is never read back), DELETE, LIST, multipart, bucket creation.

**Never prints a secret.** Errors carry the method, the bucket-relative key and
the HTTP status, never a header, a signature or the endpoint's credentials.

The PUT signs the body's real sha256 (`x-amz-content-sha256`), not
`UNSIGNED-PAYLOAD`: an S3 server that checks it refuses a body that differs.
Whether Supabase checks it is undocumented, so callers must not count it as
verification (docs/props_archive_storage.md §7).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Final, Mapping

__all__ = ["S3Config", "S3Client", "S3Error", "ObjectInfo", "sign", "ENV_NAMES"]

ALGORITHM: Final = "AWS4-HMAC-SHA256"
SERVICE: Final = "s3"

#: The root .env names (values never logged). docs/props_archive_storage.md §9.
ENV_NAMES: Final = {
    "endpoint": "PROPS_ARCHIVE_S3_ENDPOINT",
    "region": "PROPS_ARCHIVE_S3_REGION",
    "access_key_id": "PROPS_ARCHIVE_S3_ACCESS_KEY_ID",
    "secret_access_key": "PROPS_ARCHIVE_S3_SECRET_ACCESS_KEY",
    "bucket": "PROPS_ARCHIVE_BUCKET",
}
DEFAULT_BUCKET: Final = "nfl-props-archive"

#: (method, url, headers, body) -> (status, response headers, body)
Transport = Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, Mapping[str, str], bytes]]


class S3Error(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    size: int
    etag: str  # quotes stripped


@dataclass(frozen=True)
class S3Config:
    endpoint: str
    region: str
    access_key_id: str
    secret_access_key: str
    bucket: str = DEFAULT_BUCKET

    def __repr__(self) -> str:  # never let a secret reach a log line
        return f"S3Config(bucket={self.bucket!r}, region={self.region!r})"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "S3Config":
        env = os.environ if env is None else env
        values = {field: (env.get(name) or "").strip() for field, name in ENV_NAMES.items()}
        values["bucket"] = values["bucket"] or DEFAULT_BUCKET
        missing = [ENV_NAMES[f] for f, v in values.items() if not v]
        if missing:
            raise S3Error(f"not set in the root .env: {', '.join(missing)}")
        if not values["endpoint"].startswith("https://"):
            raise S3Error(f"{ENV_NAMES['endpoint']} must be an https:// URL")
        return cls(**values)


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _quote_path(path: str) -> str:
    return "/".join(urllib.parse.quote(seg, safe="-_.~") for seg in path.split("/"))


def sign(
    method: str, url: str, headers: Mapping[str, str], payload_sha256: str, *,
    access_key_id: str, secret_access_key: str, region: str, now: datetime,
) -> dict[str, str]:
    """The request's headers plus `x-amz-date`, `x-amz-content-sha256` and
    `Authorization`. Every header passed (and `host`) is signed."""
    parts = urllib.parse.urlsplit(url)
    amz_date = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    out = {k: v for k, v in headers.items()}
    out["x-amz-date"] = amz_date
    out["x-amz-content-sha256"] = payload_sha256
    canon = {k.lower(): " ".join(str(v).split()) for k, v in out.items()}
    canon["host"] = parts.netloc
    names = sorted(canon)
    query = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
        for k, v in sorted(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    )
    canonical_request = "\n".join([
        method, _quote_path(parts.path or "/"), query,
        "".join(f"{n}:{canon[n]}\n" for n in names), ";".join(names), payload_sha256,
    ])
    scope = f"{day}/{region}/{SERVICE}/aws4_request"
    to_sign = "\n".join([ALGORITHM, amz_date, scope,
                         hashlib.sha256(canonical_request.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_access_key).encode(), day), region), SERVICE),
                "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    out["Authorization"] = (f"{ALGORITHM} Credential={access_key_id}/{scope}, "
                            f"SignedHeaders={';'.join(names)}, Signature={signature}")
    return out


def _urllib_transport(method: str, url: str, headers: Mapping[str, str],
                      body: bytes | None) -> tuple[int, Mapping[str, str], bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, exc.read() or b""


class S3Client:
    def __init__(self, config: S3Config, transport: Transport | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.config = config
        self._transport = transport or _urllib_transport
        self._clock = clock

    @property
    def bucket(self) -> str:
        return self.config.bucket

    def _url(self, key: str) -> str:
        if not key or key.startswith("/") or ".." in key.split("/"):
            raise S3Error(f"refusing object key {key!r}")
        return f"{self.config.endpoint.rstrip('/')}/{self.config.bucket}/{key}"

    def _send(self, method: str, key: str, headers: Mapping[str, str], body: bytes | None,
              payload_sha256: str) -> tuple[int, Mapping[str, str], bytes]:
        url = self._url(key)
        signed = sign(method, url, headers, payload_sha256,
                      access_key_id=self.config.access_key_id,
                      secret_access_key=self.config.secret_access_key,
                      region=self.config.region, now=self._clock())
        try:
            return self._transport(method, url, signed, body)
        except OSError as exc:  # network: name the object, not the URL
            raise S3Error(f"{method} {self.bucket}/{key}: unreachable ({type(exc).__name__})") from None

    def head(self, key: str) -> ObjectInfo | None:
        """The object's size and ETag, or None if it does not exist."""
        status, headers, _ = self._send("HEAD", key, {}, None, hashlib.sha256(b"").hexdigest())
        if status == 404:
            return None
        if status != 200:
            raise S3Error(f"HEAD {self.bucket}/{key} returned HTTP {status}")
        try:
            size = int(headers.get("content-length", ""))
        except ValueError:
            raise S3Error(f"HEAD {self.bucket}/{key} had no Content-Length") from None
        return ObjectInfo(size=size, etag=headers.get("etag", "").strip('"'))

    def put(self, key: str, body: bytes, content_type: str) -> str:
        """Upload one object; returns the ETag (quotes stripped)."""
        status, headers, _ = self._send(
            "PUT", key, {"content-type": content_type, "content-length": str(len(body))},
            body, hashlib.sha256(body).hexdigest())
        if status != 200:
            raise S3Error(f"PUT {self.bucket}/{key} returned HTTP {status}")
        return headers.get("etag", "").strip('"')
