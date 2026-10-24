"""Pure-logic units: bedfeed translation, reading derivation, frame storage
path sanitizing, config parsing."""

from datetime import datetime, timezone

import pytest

from app import frame_storage
from app.bedfeed import to_bed_payload
from app.config import Settings
from app.cv import reading_builder
from app.cv.pipeline import MockFrameProcessor
from app.models import Reading
from app.schemas import ReadingOut

from .conftest import make_reading


def _reading_out(**overrides) -> ReadingOut:
    return ReadingOut(**make_reading(**overrides), id=1, received_at=datetime.now(timezone.utc))


# --- bedfeed ----------------------------------------------------------------


@pytest.mark.parametrize(
    "device_status, bed_status",
    [
        ("NORMAL", "STABLE"),
        ("LOW_LEVEL", "WATCH"),
        ("EMPTY", "CRITICAL"),
        ("OCCLUSION", "CRITICAL"),
        ("ERROR", "CRITICAL"),
        ("SOMETHING_NEW", "STABLE"),
    ],
)
def test_bedfeed_status_mapping(device_status, bed_status):
    assert to_bed_payload(_reading_out(status=device_status))["status"] == bed_status


def test_bedfeed_fallbacks_for_unassigned_device():
    payload = to_bed_payload(_reading_out(device_id="CAM-9", bed_label=None, patient_name=None, fluid_label=None))
    assert payload["bed"] == "CAM-9"
    assert payload["patient"] == "Unassigned"
    assert payload["fluid"] == "Unknown"


def test_bedfeed_rounds_to_one_decimal():
    payload = to_bed_payload(_reading_out(flow_rate_ml_per_hr=99.96, fluid_level_percent=33.333))
    assert payload["flow"] == 100.0
    assert payload["level"] == 33.3


# --- reading_builder --------------------------------------------------------


@pytest.mark.parametrize(
    "level, status, has_alert",
    [(100, "NORMAL", False), (15.1, "NORMAL", False), (15, "LOW_LEVEL", True), (0.5, "EMPTY", True), (0, "EMPTY", True)],
)
def test_build_reading_status_thresholds(db, level, status, has_alert):
    r = reading_builder.build_reading(db, "D", level)
    assert r.status == status
    assert (r.alert is not None) is has_alert


@pytest.mark.parametrize("raw, clamped", [(-20, 0.0), (140, 100.0)])
def test_build_reading_clamps_level(db, raw, clamped):
    assert reading_builder.build_reading(db, "D", raw).fluid_level_percent == clamped


def test_build_reading_first_reading_has_no_flow(db):
    r = reading_builder.build_reading(db, "D", 50, bag_capacity_ml=1000)
    assert r.volume_remaining_ml == 500.0
    assert r.flow_rate_ml_per_hr == 0.0
    assert r.estimated_time_remaining_min == 0.0


def test_build_reading_estimates_flow_from_previous(db):
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    one_hour_ago = now_ms - 3_600_000
    prev = make_reading(device_id="D", timestamp_ms=one_hour_ago, volume_remaining_ml=400.0)
    db.add(Reading(**prev))
    db.commit()

    r = reading_builder.build_reading(db, "D", 60)  # 300 ml left of 500
    assert r.flow_rate_ml_per_hr == pytest.approx(100.0, rel=0.01)
    assert r.drop_rate_per_min == pytest.approx(33.3, rel=0.01)
    assert r.estimated_time_remaining_min == pytest.approx(180.0, rel=0.01)


def test_build_reading_never_reports_negative_flow(db):
    # Bag replaced (level went *up*) - not a negative drip rate.
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    db.add(Reading(**make_reading(device_id="D", timestamp_ms=now_ms - 60_000, volume_remaining_ml=10.0)))
    db.commit()
    assert reading_builder.build_reading(db, "D", 100).flow_rate_ml_per_hr == 0.0


def test_prune_old_readings_keeps_newest(db):
    for i in range(5):
        db.add(Reading(**make_reading(device_id="D", timestamp_ms=i), received_at=datetime(2026, 1, 1, 0, i)))
    db.add(Reading(**make_reading(device_id="OTHER")))
    db.commit()

    reading_builder.prune_old_readings(db, "D", keep=2)

    kept = sorted(r.timestamp_ms for r in db.query(Reading).filter_by(device_id="D"))
    assert kept == [3, 4]
    assert db.query(Reading).filter_by(device_id="OTHER").count() == 1


def test_mock_processor_output_is_consistent():
    for _ in range(50):
        result = MockFrameProcessor().process("D", b"")
        assert 10 <= result.fill_percent <= 100
        assert result.aux_class in {"empty", "50%", "80%", "full"}


# --- frame_storage ----------------------------------------------------------


@pytest.mark.parametrize("device_id", ["../../etc/passwd", "a/b\\c", "..", "CAM 01;rm -rf"])
def test_frame_path_cannot_escape_frames_dir(device_id):
    path = frame_storage._frame_path(device_id)
    assert path.parent == frame_storage.FRAMES_DIR
    assert "/" not in path.name and "\\" not in path.name


def test_frame_path_empty_id():
    assert frame_storage._frame_path("").name == "unknown.jpg"


def test_save_and_load_frame():
    assert frame_storage.load_latest_frame("X") is None
    frame_storage.save_latest_frame("X", b"abc")
    assert frame_storage.load_latest_frame("X") == b"abc"


# --- config -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, parsed",
    [
        ("*", ["*"]),
        (" * ", ["*"]),
        ("https://a.app, https://b.app", ["https://a.app", "https://b.app"]),
        ("https://a.app,,", ["https://a.app"]),
    ],
)
def test_cors_origin_list(raw, parsed):
    assert Settings(cors_origins=raw, api_auth_token="x").cors_origin_list == parsed
