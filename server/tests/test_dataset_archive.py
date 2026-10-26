"""Training-frame archive to Supabase (app/dataset_archive.py), against a
mock Supabase - storage upload, metadata row, labels from capture_settings,
and failure handling that must never break live monitoring."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from app import dataset_archive
from app.config import settings

from .conftest import AUTH, make_jpeg

SUPABASE = "https://example.supabase.co"


class FakeSupabase:
    """Just enough of Supabase Storage + PostgREST to exercise the archiver."""

    def __init__(self, capture_settings=None, fail_upload=False, fail_insert=False):
        self.capture_settings = capture_settings if capture_settings is not None else [{"enabled": True}]
        self.fail_upload = fail_upload
        self.fail_insert = fail_insert
        self.objects: dict[str, bytes] = {}
        self.rows: list[dict] = []
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/rest/v1/capture_settings":
            return httpx.Response(200, json=self.capture_settings)
        if path.startswith("/storage/v1/object/training-frames/"):
            key = path.removeprefix("/storage/v1/object/training-frames/")
            if request.method == "DELETE":
                self.objects.pop(key, None)
                return httpx.Response(200, json={})
            if self.fail_upload:
                return httpx.Response(500, json={"error": "storage down"})
            self.objects[key] = request.content
            return httpx.Response(200, json={"Key": f"training-frames/{key}"})
        if path == "/rest/v1/training_frames":
            if self.fail_insert:
                return httpx.Response(400, json={"message": "bad row"})
            self.rows.append(json.loads(request.content))
            return httpx.Response(201)
        return httpx.Response(404)


@pytest.fixture
def supabase(monkeypatch):
    fake = FakeSupabase()
    monkeypatch.setattr(settings, "supabase_url", SUPABASE)
    monkeypatch.setattr(settings, "supabase_service_role_key", "service-key")
    monkeypatch.setattr(dataset_archive, "_settings_cache", (float("-inf"), None))
    monkeypatch.setattr(
        dataset_archive,
        "_client",
        lambda: httpx.Client(base_url=SUPABASE, transport=httpx.MockTransport(fake.handler)),
    )
    return fake


def test_disabled_without_credentials():
    assert dataset_archive.archive_enabled() is False
    assert dataset_archive.archive_frame("CAM-01", make_jpeg()) is None


def test_storage_path_is_grouped_and_sanitized():
    at = datetime(2026, 10, 26, 16, 5, 9, 123456, tzinfo=UTC)
    assert dataset_archive.storage_path("CAM-01", at) == "CAM-01/2026-10-26/CAM-01_20261026T160509123456Z.jpg"
    assert dataset_archive.storage_path("../../x y", at).startswith("______x_y/2026-10-26/")


def test_archives_image_and_row(supabase):
    frame = make_jpeg(size=(640, 480))
    path = dataset_archive.archive_frame("CAM-01", frame, 42.5, "50%")

    assert supabase.objects == {path: frame}
    [row] = supabase.rows
    assert row["storage_path"] == path
    assert row["device_id"] == "CAM-01"
    assert (row["width"], row["height"], row["bytes"]) == (640, 480, len(frame))
    assert row["predicted_fill_percent"] == 42.5
    assert row["predicted_class"] == "50%"
    assert row["label_fill_percent"] is None

    upload = next(r for r in supabase.requests if r.url.path.startswith("/storage/"))
    assert upload.headers["authorization"] == "Bearer service-key"
    assert upload.headers["apikey"] == "service-key"
    assert upload.headers["content-type"] == "image/jpeg"


def test_png_keeps_its_type(supabase):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="PNG")
    path = dataset_archive.archive_frame("CAM-01", buf.getvalue())
    assert path.endswith(".png")


def test_labels_come_from_capture_settings(supabase):
    supabase.capture_settings = [
        {"enabled": True, "session": "run-03", "label_fill_percent": 80, "label_yaw_deg": 30}
    ]
    dataset_archive.archive_frame("CAM-01", make_jpeg())
    [row] = supabase.rows
    assert (row["session"], row["label_fill_percent"], row["label_yaw_deg"]) == ("run-03", 80, 30)


def test_paused_capture_archives_nothing(supabase):
    supabase.capture_settings = [{"enabled": False}]
    assert dataset_archive.archive_frame("CAM-01", make_jpeg()) is None
    assert supabase.objects == {} and supabase.rows == []


def test_capture_settings_are_cached(supabase):
    dataset_archive.archive_frame("CAM-01", make_jpeg())
    dataset_archive.archive_frame("CAM-01", make_jpeg())
    reads = [r for r in supabase.requests if r.url.path == "/rest/v1/capture_settings"]
    assert len(reads) == 1
    assert len(supabase.rows) == 2


def test_storage_failure_is_swallowed(supabase):
    supabase.fail_upload = True
    assert dataset_archive.archive_frame("CAM-01", make_jpeg()) is None
    assert supabase.rows == []


def test_failed_insert_removes_orphan_image(supabase):
    supabase.fail_insert = True
    assert dataset_archive.archive_frame("CAM-01", make_jpeg()) is None
    assert supabase.objects == {}


def test_unreadable_bytes_are_swallowed(supabase):
    assert dataset_archive.archive_frame("CAM-01", b"not an image") is None
    assert supabase.requests == []


def test_frame_upload_endpoint_archives_after_responding(client, fake_processor, supabase):
    frame = make_jpeg()
    res = client.post(
        "/api/v1/frames",
        params={"device_id": "CAM-07"},
        files={"file": ("f.jpg", frame, "image/jpeg")},
        headers=AUTH,
    )
    assert res.status_code == 201
    [row] = supabase.rows
    assert row["device_id"] == "CAM-07"
    assert row["predicted_fill_percent"] == 72.5
    assert row["predicted_class"] == "80%"
    assert list(supabase.objects.values()) == [frame]


def test_frame_upload_endpoint_skips_archive_when_disabled(client, fake_processor, monkeypatch):
    called = []
    monkeypatch.setattr(dataset_archive, "_client", lambda: called.append(1))
    res = client.post(
        "/api/v1/frames",
        params={"device_id": "CAM-07"},
        files={"file": ("f.jpg", make_jpeg(), "image/jpeg")},
        headers=AUTH,
    )
    assert res.status_code == 201
    assert called == []
