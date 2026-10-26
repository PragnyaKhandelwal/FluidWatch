"""Archives every received camera frame to Supabase for model retraining:
the image goes to the private `training-frames` Storage bucket, and one
`training_frames` row records its metadata plus whatever ground-truth labels
are currently set in `capture_settings`. Schema and access rules:
supabase/migrations/20261026120000_training_frames.sql.

Runs as a FastAPI background task after the device already has its response,
and never raises - an archive failure must not affect live monitoring."""

import io
import logging
import re
import time
from datetime import UTC, datetime

import httpx
from PIL import Image

from .config import settings

logger = logging.getLogger(__name__)

_SAFE_DEVICE_ID = re.compile(r"[^A-Za-z0-9_-]")
_SETTINGS_TTL_S = 10.0
_FORMATS = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png")}

# (fetched_at monotonic seconds, capture_settings row or None)
_settings_cache: tuple[float, dict | None] = (float("-inf"), None)


def archive_enabled() -> bool:
    return bool(settings.supabase_url and settings.supabase_service_role_key)


def _client() -> httpx.Client:
    return httpx.Client(base_url=settings.supabase_url.rstrip("/"), timeout=15.0)


def _headers(**extra: str) -> dict[str, str]:
    key = settings.supabase_service_role_key
    return {"apikey": key, "Authorization": f"Bearer {key}", **extra}


def storage_path(device_id: str, captured_at: datetime, extension: str = "jpg") -> str:
    """`<device>/<YYYY-MM-DD>/<device>_<timestamp>.<ext>` - grouped by device
    and day so the bucket stays browsable in the Supabase dashboard."""
    safe_id = _SAFE_DEVICE_ID.sub("_", device_id) or "unknown"
    stamp = captured_at.strftime("%Y%m%dT%H%M%S%fZ")
    return f"{safe_id}/{captured_at:%Y-%m-%d}/{safe_id}_{stamp}.{extension}"


def _capture_settings(client: httpx.Client) -> dict | None:
    """The single capture_settings row, cached briefly - the ESP32 uploads
    every ~10s and labels change rarely, so this saves a request per frame
    while still picking up a new label within seconds."""
    global _settings_cache
    fetched_at, cached = _settings_cache
    if time.monotonic() - fetched_at < _SETTINGS_TTL_S:
        return cached
    res = client.get(
        "/rest/v1/capture_settings",
        params={"select": "enabled,session,label_fill_percent,label_yaw_deg"},
        headers=_headers(),
    )
    res.raise_for_status()
    rows = res.json()
    cached = rows[0] if rows else None
    _settings_cache = (time.monotonic(), cached)
    return cached


def archive_frame(
    device_id: str,
    frame_bytes: bytes,
    predicted_fill_percent: float | None = None,
    predicted_class: str | None = None,
    captured_at: datetime | None = None,
) -> str | None:
    """Uploads one frame and records it. Returns the storage path, or None
    if archiving is off, paused via capture_settings.enabled, or failed."""
    if not archive_enabled():
        return None
    captured_at = captured_at or datetime.now(UTC)
    try:
        with Image.open(io.BytesIO(frame_bytes)) as im:
            width, height = im.size
            extension, content_type = _FORMATS.get(im.format or "", _FORMATS["JPEG"])

        with _client() as client:
            capture = _capture_settings(client) or {}
            if capture.get("enabled") is False:
                return None

            path = storage_path(device_id, captured_at, extension)
            object_url = f"/storage/v1/object/{settings.dataset_bucket}/{path}"
            uploaded = client.post(object_url, content=frame_bytes, headers=_headers(**{"Content-Type": content_type}))
            uploaded.raise_for_status()

            row = {
                "storage_path": path,
                "device_id": device_id,
                "captured_at": captured_at.isoformat(),
                "bytes": len(frame_bytes),
                "width": width,
                "height": height,
                "predicted_fill_percent": predicted_fill_percent,
                "predicted_class": predicted_class,
                "label_fill_percent": capture.get("label_fill_percent"),
                "label_yaw_deg": capture.get("label_yaw_deg"),
                "session": capture.get("session"),
            }
            inserted = client.post("/rest/v1/training_frames", json=row, headers=_headers(Prefer="return=minimal"))
            if inserted.is_error:
                # Don't leave an image in the bucket with no row describing it.
                client.delete(object_url, headers=_headers())
                inserted.raise_for_status()
            return path
    except Exception:
        logger.exception("training-frame archive failed for device=%s", device_id)
        return None
