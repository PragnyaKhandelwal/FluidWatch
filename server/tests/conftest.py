"""Shared fixtures. The env vars below must be set before anything under
app/ is imported - app/config.py reads them at import time, and without
API_AUTH_TOKEN it would generate a token and write it into the real .env."""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="iv-drip-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["API_AUTH_TOKEN"] = "test-token"
os.environ["CORS_ORIGINS"] = "*"
# Never archive to a real Supabase project from tests, even if server/.env
# has credentials - test_dataset_archive.py enables it against a mock.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import frame_storage  # noqa: E402
from app.cv.base import FrameProcessor, FrameResult  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import frames  # noqa: E402

AUTH = {"Authorization": "Bearer test-token"}


class FixedFrameProcessor(FrameProcessor):
    """Deterministic stand-in for the real model, so API tests can assert
    exact numbers. The real model has its own tests in test_cv_model.py."""

    def __init__(self, fill_percent: float = 72.5, aux_class: str | None = "80%"):
        self.fill_percent = fill_percent
        self.aux_class = aux_class
        self.calls: list[str] = []

    def process(self, device_id: str, frame_bytes: bytes) -> FrameResult:
        self.calls.append(device_id)
        return FrameResult(fill_percent=self.fill_percent, aux_class=self.aux_class)


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def _tmp_frames_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_storage, "FRAMES_DIR", tmp_path / "frames")


@pytest.fixture
def fake_processor(monkeypatch) -> FixedFrameProcessor:
    processor = FixedFrameProcessor()
    monkeypatch.setattr(frames, "active_processor", processor)
    return processor


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def make_reading(**overrides) -> dict:
    """A valid POST /api/v1/readings body - what the firmware sends."""
    body = {
        "device_id": "IV-STAND-01",
        "timestamp_ms": 1_000,
        "fluid_level_percent": 80.0,
        "volume_remaining_ml": 400.0,
        "bag_capacity_ml": 500.0,
        "flow_rate_ml_per_hr": 120.0,
        "drop_rate_per_min": 40.0,
        "estimated_time_remaining_min": 200.0,
        "battery_percent": 90,
        "wifi_rssi": -55,
        "status": "NORMAL",
        "alert": None,
        "bed_label": "Bed 07",
        "patient_name": "Test Patient",
        "fluid_label": "Saline 0.9%",
    }
    body.update(overrides)
    return body


def make_jpeg(color=(40, 120, 200), size=(320, 240)) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()
